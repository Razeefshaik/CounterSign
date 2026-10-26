"""Read-only evidence for a configured demo whose safe actions may happen early.

The script compares resource GETs with stored identities and never grants write
permission. Available-cash equality can lag a confirmed movement; callers wait
for ``cash_matches`` instead of declaring an old snapshot an invariant breach.
"""

import copy
import json
from datetime import datetime

from countersign.db import Repositories
from countersign.money import Money
from countersign.reconcile.attempts import _matches
from scripts.slice_e2e import SliceError, require

OUTSTANDING = frozenset({"PENDING", "ONHOLD", "BLOCKED", "UNCLAIMED", "SUCCESS"})
RETURNED = frozenset({"RETURNED", "REFUNDED", "REVERSED"})
KNOWN = OUTSTANDING | RETURNED | {"FAILED"}
_TABLES = ("payout_attempts", "payout_lines", "payout_line_obligations", "obligations",
           "disputes", "ledger_journals")


def _time(value):
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)


def _available(snapshot):
    cash = snapshot.get("cash")
    value = cash.get("paypal", {}).get("available") if isinstance(cash, dict) else None
    if not isinstance(value, dict) or value.get("currency") != "USD":
        return None
    return value.get("minor") if type(value.get("minor")) is int else None


def _evidence(state, attempts, items, disputes, dispute_details, rows, now):
    """Update ID-keyed confirmed facts; a resumed GET never adds money twice."""
    result = copy.deepcopy(state)
    result.setdefault("items", {})
    result.setdefault("refunds", {})
    result.setdefault("successes", {})
    complete = True
    for attempt in attempts:
        identifier = str(attempt["id"])
        if not attempt.get("paypal_item_id"):
            complete &= attempt["state"] == "REJECTED"
            continue
        detail = items.get(identifier)
        if detail is None:
            complete = False
            continue
        require(attempt["currency"].strip() == "USD"
                and _matches(attempt, detail, item_id=attempt["paypal_item_id"]),
                "CONFIGURED_PAYOUT_IDENTITY_MISMATCH")
        status = detail.get("transaction_status")
        require(status in KNOWN, "CONFIGURED_PAYOUT_STATE_UNKNOWN")
        previous = result["items"].get(identifier, {})
        fact = {"line_id": str(attempt["line_id"]), "item_id": attempt["paypal_item_id"],
                "amount_minor": attempt["amount_minor"], "status": status,
                "observed_at": now.isoformat()}
        if status in RETURNED:
            first = previous.get("returned_at")
            # On upgrading an old checkpoint, the reconciler's return journal
            # preserves the earlier GET confirmation even if last_checked_at
            # has since advanced. This is never inferred from a cancel POST.
            journals = [j["occurred_at"] for j in rows["ledger_journals"]
                        if j["operation_id"] == attempt["operation_id"]
                        and j["memo"].startswith("PAYOUT_RETURNED:")]
            choices = [now, *journals, *([_time(first)] if first else [])]
            fact["returned_at"] = min(choices).isoformat()
        elif previous.get("returned_at"):
            fact["returned_at"] = previous["returned_at"]
        result["items"][identifier] = fact
        if status == "SUCCESS":
            line = str(attempt["line_id"])
            successes = set(result["successes"].get(line, [])) | {identifier}
            require(len(successes) == 1, "CONFIGURED_LINE_PAID_TWICE")
            result["successes"][line] = sorted(successes)

    by_obligation = {str(o["id"]): o for o in rows["obligations"]}
    protected_orders = {str(d["order_id"]) for d in disputes
                        if d.get("order_id") and d["phase"] in {"ACTIVE", "UNKNOWN_OUTCOME"}}
    for attempt in attempts:
        identifier = str(attempt["id"])
        fact = result["items"].get(identifier)
        if fact and fact["status"] in OUTSTANDING:
            for link in rows["payout_line_obligations"]:
                if link["line_id"] != attempt["line_id"]:
                    continue
                obligation = by_obligation.get(str(link["obligation_id"]))
                require(obligation is not None, "CONFIGURED_PAYOUT_OBLIGATION_MISSING")
                require(obligation["state"] != "RESERVED"
                        and str(obligation.get("order_id")) not in protected_orders,
                        "CONFIGURED_DISPUTED_SHARE_PAID")
        if attempt["attempt_no"] > 1:
            prior = [a for a in attempts if a["line_id"] == attempt["line_id"]
                     and a["attempt_no"] == attempt["attempt_no"] - 1]
            require(len(prior) == 1, "CONFIGURED_REPLACEMENT_PREDECESSOR_MISSING")
            confirmed = result["items"].get(str(prior[0]["id"]), {})
            require(confirmed.get("status") == "RETURNED" and confirmed.get("returned_at")
                    and _time(confirmed["returned_at"]) <= attempt["prepared_at"],
                    "CONFIGURED_REPLACEMENT_BEFORE_RETURN")

    for dispute in disputes:
        identifier = dispute["paypal_dispute_id"]
        detail = dispute_details.get(identifier)
        if detail is None:
            complete = False
            continue
        require(detail.get("dispute_id") == identifier, "CONFIGURED_DISPUTE_IDENTITY_MISMATCH")
        refunded = (detail.get("dispute_outcome") or {}).get("amount_refunded")
        amount = Money.from_paypal(refunded) if refunded is not None else Money.zero("USD")
        require(amount.currency == "USD" and amount.minor >= 0,
                "CONFIGURED_REFUND_AMOUNT_INVALID")
        before = result["refunds"].get(identifier, 0)
        require(amount.minor >= before, "CONFIGURED_REFUND_REGRESSED")
        result["refunds"][identifier] = amount.minor

    expected = result["start_available_minor"]
    for identifier, fact in result["items"].items():
        baseline = result["baseline_items"].get(identifier)
        amount = fact["amount_minor"]
        if baseline in OUTSTANDING:
            expected += amount if fact["status"] in RETURNED | {"FAILED"} else 0
        elif baseline is None:
            expected -= amount if fact["status"] in OUTSTANDING else 0
    expected -= sum(result["refunds"].values()) - sum(result["baseline_refunds"].values())
    require(expected >= 0, "CONFIGURED_NEGATIVE_CASH")
    result["expected_available_minor"] = expected
    return result, complete


class ConfiguredWeekObserver:
    """Observe an isolated configured demo through GETs and read-only repositories."""

    def __init__(self, database, http, store):
        self.database, self.http, self.store = database, http, store

    async def _rows(self):
        async with self.database.tx() as conn:
            repos = Repositories(conn)
            return {table: await getattr(repos, table).find(limit=1000) for table in _TABLES}

    async def start(self, snapshot):
        if self.store.data.get("configured_cash_evidence") is not None:
            return
        rows = await self._rows()
        available = _available(snapshot)
        initial_proved = available == 150000
        if not initial_proved:
            # Old owner checkpoints already recorded Monday's GET-backed cash.
            # Read only their public event log, never environment files/secrets.
            path = self.store.directory / "run.jsonl"
            if path.is_file():
                for line in path.read_text(encoding="utf-8").splitlines():
                    event = json.loads(line)
                    if (event.get("event") == "MONDAY_DISPUTE_FROZEN"
                            and event.get("available_minor") == 150000):
                        initial_proved = True
                        break
        require(initial_proved, "CONFIGURED_INITIAL_CASH_UNCONFIRMED")
        # Only the pre-existing imported item belongs to the starting balance.
        # Newly submitted attempts must remain outflows even after a resume.
        baseline = {str(a["id"]): "UNCLAIMED" for a in rows["payout_attempts"]
                    if a.get("paypal_item_id") == self.store.data.get("prior_item_id")}
        require(len(baseline) == 1, "CONFIGURED_PRIOR_PAYOUT_MISSING")
        self.store.checkpoint(configured_cash_evidence={
            "start_available_minor": 150000, "baseline_items": baseline,
            "baseline_refunds": {}, "items": {}, "refunds": {}, "successes": {},
        })

    async def observe(self, snapshot):
        require(self.store.data.get("configured_cash_evidence") is not None,
                "CONFIGURED_CASH_NOT_STARTED")
        rows = await self._rows()
        items, details = {}, {}
        for attempt in rows["payout_attempts"]:
            if attempt.get("paypal_item_id"):
                response = await self.http.request(
                    "GET", "/v1/payments/payouts-item/" + attempt["paypal_item_id"])
                if response.status_code == 200 and isinstance(response.body, dict):
                    items[str(attempt["id"])] = response.body
        for dispute in rows["disputes"]:
            identifier = dispute["paypal_dispute_id"]
            response = await self.http.request("GET", "/v1/customer/disputes/" + identifier)
            if response.status_code == 200 and isinstance(response.body, dict):
                details[identifier] = response.body
        try:
            updated, complete = _evidence(self.store.data["configured_cash_evidence"],
                rows["payout_attempts"], items, rows["disputes"], details, rows, self.store.clock())
        except (TypeError, ValueError, KeyError, AttributeError):
            raise SliceError("CONFIGURED_RESOURCE_EVIDENCE_INVALID") from None
        actual = _available(snapshot)
        expected = updated["expected_available_minor"]
        report = {"cash_matches": complete and actual is not None and actual == expected,
                  "expected_available_minor": expected, "actual_available_minor": actual,
                  "resources_confirmed": complete}
        if self.store.data.get("configured_cash_report") != report:
            self.store.record("CONFIGURED_MODEL_CASH", **report,
                              start_available_minor=updated["start_available_minor"],
                              formula="start + GET-confirmed returns - GET-confirmed payouts/refunds")
        self.store.checkpoint(configured_cash_evidence=updated, configured_cash_report=report)
        return report
