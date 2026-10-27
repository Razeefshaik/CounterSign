"""Transactional owner decisions. No network calls and no approval consumption.

The pending binding is stored in proposals.verdict_details.approval_request. Payloads
are rebuilt from locked authoritative rows, never from an agent-supplied recipient.
"""

import re
from collections import defaultdict
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

from psycopg import sql

from countersign.approvals.requests import build_request
from countersign.config import PayPalBehavior
from countersign.db import Database, Repositories, cas_transition
from countersign.fsm.line import LINE, LineEvent
from countersign.fsm.obligation import OBLIGATION, ObligationEvent
from countersign.fsm.proposal import PROPOSAL, VERDICT_EVENT, ProposalEvent
from countersign.ledger.cash import BalanceView, Outflow, Reservation, cash_position
from countersign.ledger.journal import serialize_on
from countersign.ledger.reservations import create_reservation, release_reservation
from countersign.money import Money
from countersign.paypal.http import PayPalDataError, canonical_payload_hash
from countersign.reconcile.attempts import _matches
from countersign.shield import snapshot as model
from countersign.shield.engine import Verdict, evaluate
from countersign.shield.rules.idempotency import derive_keys

RUN_REQUEST_LIFETIME = timedelta(hours=24)


class ApprovalError(RuntimeError):
    """A public, value-free error suitable for an HTTP response."""

    def __init__(self, code: str, status: int = 409):
        super().__init__(code)
        self.code, self.status = code, status


def hash_bytes(value: str) -> bytes:
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
        raise ApprovalError("STALE_PROPOSAL")
    return bytes.fromhex(value[7:])


def _id(value):
    return str(value) if value is not None else None


def _money(row):
    return Money(row["amount_minor"], row["currency"].strip())


async def _rows(conn, table):
    # The fixed table names are private constants at call sites, never HTTP input.
    cursor = await conn.execute(
        sql.SQL("SELECT * FROM {} ORDER BY id FOR UPDATE").format(sql.Identifier(table))
    )
    return await cursor.fetchall()


def _returned(attempt):
    """Only an identity-matched item GET establishes the returned funds."""
    return (
        attempt is not None
        and attempt["state"] == "RETURNED"
        and attempt["last_checked_at"] is not None
        and isinstance(attempt["paypal_raw"], dict)
        and attempt["paypal_raw"].get("transaction_status") == "RETURNED"
        and bool(attempt["paypal_item_id"])
        and _matches(attempt, attempt["paypal_raw"], item_id=attempt["paypal_item_id"])
    )


def _replacement_ids(params, data, run_id):
    """Bind every replacement field to locked records, including its next keys."""
    entries = params.get("replacements", [])
    if not isinstance(entries, list):
        raise ApprovalError("STALE_PROPOSAL")
    result = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {
            "line_id", "maker_id", "amount", "attempt_no"
        }:
            raise ApprovalError("STALE_PROPOSAL")
        line_id = entry["line_id"]
        found = [line for line in data["payout_lines"] if str(line["id"]) == line_id]
        if len(found) != 1 or line_id in result:
            raise ApprovalError("STALE_PROPOSAL")
        line = found[0]
        amount = entry["amount"]
        if (
            not isinstance(amount, dict) or set(amount) != {"minor", "currency"}
            or type(amount["minor"]) is not int
        ):
            raise ApprovalError("STALE_PROPOSAL")
        attempts = [a for a in data["payout_attempts"] if a["line_id"] == line["id"]]
        latest = max(attempts, key=lambda a: a["attempt_no"], default=None)
        if (
            str(line["run_id"]) == run_id
            or line["state"] != "UNPAID"
            or entry["maker_id"] != str(line["maker_id"])
            or entry["amount"] != {"minor": line["amount_minor"], "currency": line["currency"].strip()}
            or type(entry["attempt_no"]) is not int
            or not _returned(latest)
            or entry["attempt_no"] != latest["attempt_no"] + 1
        ):
            raise ApprovalError("STALE_PROPOSAL")
        result.add(line_id)
    return result


async def load_rule_facts(conn, snap, *, data, proposal_row):
    """Enrich a snapshot with locked, authoritative C24 facts; never alter payloads.

    Unknown refund dates count against today's budget conservatively. Committed
    and ambiguous accept-claims reserve their full dispute amount; observed refunds
    on the same dispute are counted once using the larger amount.
    """
    cursor = await conn.execute("SELECT key,value FROM policies ORDER BY key FOR SHARE")
    policies = {r["key"]: r["value"] for r in await cursor.fetchall()}
    enriched = []
    totals = {"orders": {}, "day": {}}
    for row in data["disputes"]:
        raw = row["raw"] if isinstance(row["raw"], dict) else {}
        transactions = raw.get("disputed_transactions", [])
        enriched.append(model.Dispute(
            str(row["id"]), _id(row["order_id"]), row["phase"], row["version"],
            status=row["status"], amount=_money(row) if row["amount_minor"] is not None else None,
            seller_response_due_date=row["seller_response_due_date"],
            links=tuple(row["links"] or ()), paypal_dispute_id=row["paypal_dispute_id"],
            seller_transaction_ids=tuple(
                t["seller_transaction_id"] for t in transactions
                if isinstance(t, dict) and isinstance(t.get("seller_transaction_id"), str)
            ) if isinstance(transactions, list) else (),
        ))
        currency = row["currency"].strip() if row["currency"] else None
        if currency is None:
            if row["amount_refunded_minor"]:
                totals["complete"] = False
            continue
        observed = row["amount_refunded_minor"] or 0
        committed = 0
        today = False
        for op in data["operations"]:
            if (op["kind"] != "DISPUTE_ACCEPT_CLAIM"
                    or op["state"] not in {"PENDING", "IN_FLIGHT", "UNKNOWN", "SUCCEEDED"}
                    or op["proposal_id"] == proposal_row["id"]
                    or op["target_id"] != row["id"]):
                continue
            committed += row["amount_minor"] or 0
            at = op["submitted_at"] or op["created_at"]
            today |= (op["state"] in {"PENDING", "IN_FLIGHT", "UNKNOWN"} or at is None
                      or at.astimezone(UTC).date() == snap.now.astimezone(UTC).date())
        amount = max(observed, committed)
        key = f"{row['order_id']}:{currency}"
        if row["order_id"] is not None:
            totals["orders"][key] = totals["orders"].get(key, 0) + amount
        try:
            at = datetime.fromisoformat(raw["update_time"])
            if at.utcoffset() is None:
                raise ValueError
            today |= observed > 0 and at.astimezone(UTC).date() == snap.now.astimezone(UTC).date()
        except (KeyError, TypeError, ValueError):
            today |= observed > 0
        if today:
            totals["day"][currency] = totals["day"].get(currency, 0) + amount
    return replace(
        snap, disputes=tuple(enriched), policies=policies,
        orders=tuple(data["orders"]),
        evidence_refs=tuple(proposal_row.get("evidence_refs") or ()), refund_totals=totals,
    )


async def load_context(conn, row: dict, *, now: datetime | None = None, behavior=None):
    """Return (shield Proposal, Snapshot, canonical payload) within a caller's tx.

    Lock order is makers, orders, obligations, payout_runs, payout_lines, payout_attempts,
    disputes, operations, reservations, balance_snapshots. All C18 writers first take
    the transaction advisory lock `countersign:owner-decisions`. The executor must
    retain locks until its own transaction commits and re-check all shield rules.
    """
    now = now or datetime.now(UTC)
    data = {
        table: await _rows(conn, table)
        for table in (
            "makers",
            "orders",
            "obligations",
            "payout_runs",
            "payout_lines",
            "payout_attempts",
            "disputes",
            "operations",
            "reservations",
            "balance_snapshots",
        )
    }
    cursor = await conn.execute(
        "SELECT * FROM payout_line_obligations WHERE active ORDER BY line_id, obligation_id "
        "FOR UPDATE"
    )
    links = await cursor.fetchall()
    allocated = {str(r["obligation_id"]): str(r["line_id"]) for r in links}
    params = row["params"]
    if not isinstance(params, dict):
        raise ApprovalError("STALE_PROPOSAL")
    try:
        action = model.Action(row["action"])
    except (ValueError, TypeError):
        raise ApprovalError("STALE_PROPOSAL") from None
    direct_targets = {
        key: params.get(key) for key in ("line_id", "obligation_id", "attempt_id", "dispute_id")
    }
    # Public proposals use PayPal ids; the shield binds internal row ids and versions.
    # Resolve both forms against locked records, refusing conflicting identities.
    for external, table, column, target in (
        ("paypal_dispute_id", "disputes", "paypal_dispute_id", "dispute_id"),
        ("payout_item_id", "payout_attempts", "paypal_item_id", "attempt_id"),
    ):
        if params.get(external) is not None:
            matches = [item for item in data[table] if item[column] == params[external]]
            if len(matches) != 1 or (
                direct_targets[target] is not None
                and str(direct_targets[target]) != str(matches[0]["id"])
            ):
                raise ApprovalError("STALE_PROPOSAL")
            direct_targets[target] = str(matches[0]["id"])
    if (
        action in {model.Action.ACCEPT_CLAIM, model.Action.PROVIDE_EVIDENCE}
        and not direct_targets["dispute_id"]
    ):
        raise ApprovalError("STALE_PROPOSAL")
    run_id = params.get("run_id")
    replacement_ids = (
        _replacement_ids(params, data, run_id)
        if action is model.Action.SCHEDULE_PAYOUT_RUN else set()
    )
    lines = [
        r
        for r in data["payout_lines"]
        if (
            (run_id is not None and str(r["run_id"]) == run_id)
            or str(r["id"]) == params.get("line_id")
            or str(r["id"]) in replacement_ids
        )
    ]
    makers = {str(r["id"]): r for r in data["makers"]}
    payouts, bodies = [], []
    if action in model.PAYING:
        for line in lines:
            attempts = [r for r in data["payout_attempts"] if r["line_id"] == line["id"]]
            attempt_no = max((r["attempt_no"] for r in attempts), default=0) + 1
            key = derive_keys(str(line["id"]), attempt_no)
            maker = makers[str(line["maker_id"])]
            amount = _money(line)
            payouts.append(
                model.PayoutItem(
                    str(line["id"]),
                    attempt_no,
                    str(line["maker_id"]),
                    amount,
                    maker["payout_email"],
                    key.sender_batch_id,
                    key.request_id,
                    key.sender_item_id,
                )
            )
            bodies.append(
                {
                    "line_id": str(line["id"]),
                    "request_id": key.request_id,
                    "body": {
                        "sender_batch_header": {"sender_batch_id": key.sender_batch_id},
                        "items": [
                            {
                                "recipient_type": "EMAIL",
                                "receiver": maker["payout_email"],
                                "amount": amount.to_paypal("currency"),
                                "sender_item_id": key.sender_item_id,
                            }
                        ],
                    },
                }
            )
    payload = {"action": str(action), "params": params}
    request_unavailable = False
    if action in model.PAYING:
        payload["payouts"] = bodies
    elif action in {model.Action.CANCEL_UNCLAIMED_PAYOUT, model.Action.PROVIDE_EVIDENCE,
                    model.Action.ACCEPT_CLAIM}:
        try:
            payload["request"] = build_request(
                row, action, data, direct_targets, behavior or PayPalBehavior())
        except (ValueError, TypeError, AttributeError, PayPalDataError):
            # Initial proposals must be auditable when the shield rejects an
            # unavailable action. Ready proposals still require the strict
            # executable request, including every execution-time precondition.
            if row["state"] != "CREATED":
                raise ApprovalError("ACTION_PRECONDITION_FAILED") from None
            request_unavailable = True
    currency = payouts[0].amount.currency if payouts else "USD"
    balances = sorted(
        (r for r in data["balance_snapshots"] if r["currency"].strip() == currency),
        key=lambda r: (r["fetched_at"], str(r["id"])),
        reverse=True,
    )
    # A missing cash observation blocks payouts; non-paying actions need no cash.
    if action in model.PAYING and (not balances or balances[0]["available_minor"] is None):
        raise ApprovalError("CASH_UNKNOWN")
    balance = balances[0] if balances else None
    outflows = []
    for op in data["operations"]:
        if op["submitted_at"] is None or op["kind"] != "PAYOUT_CREATE":
            continue
        for attempt in data["payout_attempts"]:
            if attempt["operation_id"] == op["id"] and attempt["currency"].strip() == currency:
                outflows.append(Outflow(_money(attempt), op["submitted_at"]))
    # Refund outflows are recoverable from the balanced journal's cash credit;
    # do not count withheld credits again against available cash.
    cursor = await conn.execute(
        "SELECT p.amount_minor, j.occurred_at FROM ledger_postings p "
        "JOIN ledger_journals j ON j.id=p.journal_id "
        "WHERE p.account='paypal:available' AND p.amount_minor<0 AND p.currency=%s "
        "AND j.memo LIKE 'REFUND:%%'",
        (currency,),
    )
    outflows += [
        Outflow(Money(-r["amount_minor"], currency), r["occurred_at"])
        for r in await cursor.fetchall()
    ]
    cash = cash_position(
        BalanceView(
            Money(balance["available_minor"] or 0, currency) if balance else Money.zero(currency),
            balance["last_refresh_time"] if balance else None,
        ),
        outflows,
        [
            Reservation(r["kind"], _money(r))
            for r in data["reservations"]
            if r["state"] == "ACTIVE" and r["currency"].strip() == currency
        ],
    )

    def targets(key):
        value = direct_targets.get(key)
        return (str(value),) if value else ()

    snap = model.Snapshot(
        now,
        cash,
        payouts=tuple(payouts),
        targets=model.Targets(
            tuple(str(r["id"]) for r in lines) or targets("line_id"),
            targets("obligation_id"),
            targets("attempt_id"),
            targets("dispute_id"),
        ),
        obligations=tuple(
            model.Obligation(
                str(r["id"]),
                str(r["maker_id"]),
                _id(r["order_id"]),
                r["state"],
                _money(r),
                r["version"],
                allocated.get(str(r["id"])),
            )
            for r in data["obligations"]
        ),
        lines=tuple(
            model.Line(
                str(r["id"]),
                str(r["run_id"]),
                str(r["maker_id"]),
                r["state"],
                _money(r),
                r["version"],
            )
            for r in data["payout_lines"]
        ),
        makers=tuple(
            model.Maker(
                str(r["id"]), r["payout_email"], r["payout_email_verified_at"], r["version"]
            )
            for r in data["makers"]
        ),
        disputes=tuple(
            model.Dispute(str(r["id"]), _id(r["order_id"]), r["phase"], r["version"])
            for r in data["disputes"]
        ),
        attempts=tuple(
            model.Attempt(
                str(r["id"]),
                str(r["line_id"]),
                r["attempt_no"],
                r["state"],
                str(r["operation_id"]),
                r["sender_batch_id"],
                r["request_id"],
                r["sender_item_id"],
                r["payee_email"],
                _money(r),
                r["prepared_at"],
                r["last_checked_at"],
                r["version"],
            )
            for r in data["payout_attempts"]
        ),
        operations=tuple(
            model.Operation(
                str(r["id"]),
                r["kind"],
                r["state"],
                "sha256:" + bytes(r["payload_hash"]).hex(),
                _id(r["proposal_id"]),
                _id(r["target_id"]),
            )
            for r in data["operations"]
        ),
        reservations=tuple(
            model.ReservationRecord(
                str(r["id"]),
                r["kind"],
                r["state"],
                _money(r),
                _id(r["obligation_id"]),
                _id(r["dispute_id"]),
                _id(r["proposal_id"]),
                _id(r["run_id"]),
            )
            for r in data["reservations"]
        ),
    )
    snap = await load_rule_facts(conn, snap, data=data, proposal_row=row)
    proposal = model.Proposal(
        str(row["id"]), action, params, canonical_payload_hash(payload),
        flags=tuple((row.get("raw_output") or {}).get("flags", ())),
    )
    if request_unavailable and evaluate(proposal, snap).verdict is not Verdict.BLOCK:
        raise ApprovalError("ACTION_PRECONDITION_FAILED")
    return proposal, snap, payload


class ApprovalService:
    def __init__(self, database: Database, *, clock=None, lifetime=timedelta(minutes=30),
                 behavior=None):
        self.behavior = behavior or PayPalBehavior()
        self.database, self.clock, self.lifetime = (
            database,
            clock or (lambda: datetime.now(UTC)),
            lifetime,
        )

    async def _lock(self, conn):
        await serialize_on(conn, "countersign:owner-decisions")

    async def _plan_replacements(self, conn, week_start, open_obligations):
        """Reuse returned historical lines; never fabricate another obligation."""
        data = {
            table: await _rows(conn, table)
            for table in ("payout_lines", "payout_attempts", "obligations", "proposals")
        }
        cursor = await conn.execute(
            "SELECT * FROM payout_line_obligations WHERE active ORDER BY line_id,obligation_id "
            "FOR UPDATE"
        )
        links = await cursor.fetchall()
        claimed = set()
        for proposal in data["proposals"]:
            if proposal["state"] not in {"CREATED", "NEEDS_OWNER", "ALLOWED", "APPROVED", "EXECUTING"}:
                continue
            params = proposal["params"] or {}
            if proposal["action"] == "SCHEDULE_PAYOUT_RUN":
                claimed.update(
                    entry.get("line_id") for entry in params.get("replacements", [])
                    if isinstance(entry, dict)
                )
            elif proposal["action"] == "CREATE_REPLACEMENT_PAYOUT":
                claimed.add(params.get("line_id"))
        # A separate historical line cannot be resized to absorb new adjustments.
        # Defer it whenever this maker has an offset that needs netting first.
        negative_makers = {o["maker_id"] for o in open_obligations if o["amount_minor"] < 0}
        obligations = {o["id"]: o for o in data["obligations"]}
        replacements = []
        for line in data["payout_lines"]:
            if (
                line["state"] != "UNPAID" or str(line["id"]) in claimed
                or line["maker_id"] in negative_makers or line["amount_minor"] <= 0
            ):
                continue
            attempts = [a for a in data["payout_attempts"] if a["line_id"] == line["id"]]
            latest = max(attempts, key=lambda a: a["attempt_no"], default=None)
            allocation = [link for link in links if link["line_id"] == line["id"]]
            if not _returned(latest) or not allocation:
                continue
            shares = [obligations[link["obligation_id"]] for link in allocation]
            if (
                any(
                    share["state"] != "SCHEDULED" or share["maker_id"] != line["maker_id"]
                    or share["currency"] != line["currency"]
                    or share["due_date"] > week_start + timedelta(days=6)
                    or share["amount_minor"] != link["amount_minor"]
                    for share, link in zip(shares, allocation, strict=True)
                )
                or sum(share["amount_minor"] for share in shares) != line["amount_minor"]
            ):
                continue
            replacements.append({
                "line_id": str(line["id"]), "maker_id": str(line["maker_id"]),
                "amount": {"minor": line["amount_minor"], "currency": line["currency"].strip()},
                "attempt_no": latest["attempt_no"] + 1,
            })
        return replacements

    async def plan_run(self, week_start: date) -> dict:
        async with self.database.tx() as conn:
            await self._lock(conn)
            return await self._plan_run(conn, week_start)

    async def _plan_run(self, conn, week_start: date) -> dict:
        """Caller owns the transaction and owner-decision lock, including refresh."""
        repos = Repositories(conn)
        if any(run["state"] != "CANCELLED" for run in
               await repos.payout_runs.find({"week_start": week_start})):
            raise ApprovalError("RUN_ALREADY_EXISTS")
        # Row locks plus the unique active-allocation index protect rival planners.
        cursor = await conn.execute(
            "SELECT o.* FROM obligations o WHERE o.state='OPEN' AND o.amount_minor<>0 "
            "AND o.due_date<=%s AND NOT EXISTS (SELECT 1 FROM payout_line_obligations p "
            "WHERE p.obligation_id=o.id AND p.active) AND NOT EXISTS "
            "(SELECT 1 FROM reservations r WHERE r.obligation_id=o.id "
            "AND r.kind='DISPUTE_EARMARK' AND r.state='ACTIVE') AND NOT EXISTS "
            "(SELECT 1 FROM disputes d WHERE d.order_id=o.order_id AND d.phase IN "
            "('ACTIVE','UNKNOWN_OUTCOME')) ORDER BY o.id FOR UPDATE",
            (week_start + timedelta(days=6),),
        )
        obligations = await cursor.fetchall()
        replacements = await self._plan_replacements(conn, week_start, obligations)
        grouped = defaultdict(list)
        planned_lines = []
        for obligation in obligations:
            grouped[obligation["maker_id"]].append(obligation)
        # F16 adjustments reduce what we owe; pay only a strictly positive net.
        # Keep nonpositive maker balances OPEN so future earnings can offset them.
        grouped = {
            maker: shares
            for maker, shares in grouped.items()
            if sum(share["amount_minor"] for share in shares) > 0
        }
        obligations = [share for shares in grouped.values() for share in shares]
        if not obligations and not replacements:
            raise ApprovalError("NO_PAYABLE_OBLIGATIONS")
        currencies = {r["currency"].strip() for r in obligations} | {
            r["amount"]["currency"] for r in replacements
        }
        if len(currencies) != 1:
            raise ApprovalError("MIXED_CURRENCY_RUN")
        currency = currencies.pop()
        total = sum(r["amount_minor"] for r in obligations) + sum(
            r["amount"]["minor"] for r in replacements
        )
        run = await repos.payout_runs.insert(week_start=week_start, state="AWAITING_OWNER")
        for maker_id, shares in sorted(grouped.items()):
            line = await repos.payout_lines.insert(
                run_id=run["id"],
                maker_id=maker_id,
                amount_minor=sum(r["amount_minor"] for r in shares),
                currency=shares[0]["currency"],
                state="APPROVED",
            )
            planned_lines.append(
                {
                    "line_id": str(line["id"]),
                    "maker_id": str(maker_id),
                    "amount": {
                        "minor": line["amount_minor"],
                        "currency": line["currency"].strip(),
                    },
                }
            )
            for obligation in shares:
                await repos.payout_line_obligations.insert(
                    line_id=line["id"],
                    obligation_id=obligation["id"],
                    amount_minor=obligation["amount_minor"],
                )
                await cas_transition(
                    conn,
                    OBLIGATION,
                    "obligations",
                    obligation["id"],
                    obligation["state"],
                    obligation["version"],
                    ObligationEvent.LINE_APPROVED,
                )
        row = await repos.proposals.insert(
            action="SCHEDULE_PAYOUT_RUN",
            params={"run_id": str(run["id"]), "lines": planned_lines, "replacements": replacements},
            evidence_refs=[],
            model="deterministic-planner",
            raw_output={},
            rationale="Pay eligible open obligations once",
            expected_effect={
                "available_change": {
                    "minor": -total,
                    "currency": currency,
                }
            },
            state="CREATED",
        )
        proposal, snap, payload = await load_context(conn, row, now=self.clock(), behavior=self.behavior)
        decision = evaluate(proposal, snap)
        if decision.verdict is Verdict.BLOCK:
            raise ApprovalError("SHIELD_BLOCKED")
        details = {
            "rule_results": decision.rule_results(),
            "approval_request": {
                "payload_hash": proposal.payload_hash,
                "record_versions": snap.record_versions(),
                "expires_at": (snap.now + RUN_REQUEST_LIFETIME).isoformat(),
            },
            "operation_payload": payload,
        }
        await repos.proposals.update(
            row["id"],
            verdict=str(decision.verdict),
            verdict_rules=[str(r) for r in decision.rules],
            verdict_details=details,
            raw_output={
                "approval_request": details["approval_request"],
                "operation_payload": payload,
            },
        )
        current = await repos.proposals.get(row["id"])
        await cas_transition(
            conn,
            PROPOSAL,
            "proposals",
            row["id"],
            current["state"],
            current["version"],
            VERDICT_EVENT[str(decision.verdict)],
        )
        await create_reservation(
            conn,
            "PROPOSAL_CASH",
            sum((i.amount for i in snap.payouts), Money.zero(snap.cash.unreserved.currency)),
            proposal_id=row["id"],
            run_id=run["id"],
        )
        result = await repos.payout_runs.get(run["id"])
        return {**result, "proposal_id": row["id"]}

    async def refresh_run_requests(self) -> int:
        """Expire and replace unapproved stale runs atomically, never owner rejections.

        The selected rows are fixed before planning: a newly created request is
        never refreshed again in this pass. A savepoint rolls back a refused
        replan while retaining the old request's release; unexpected failures
        roll back the whole transaction, so a crash cannot strand half a refresh.
        """
        refreshed = 0
        async with self.database.tx() as conn:
            await self._lock(conn)
            repos = Repositories(conn)
            cursor = await conn.execute(
                "SELECT p.* FROM proposals p JOIN payout_runs r "
                "ON r.id::text=p.params->>'run_id' "
                "WHERE p.action='SCHEDULE_PAYOUT_RUN' AND p.state='NEEDS_OWNER' "
                "AND r.state='AWAITING_OWNER' ORDER BY p.created_at,p.id FOR UPDATE OF p,r"
            )
            pending_rows = await cursor.fetchall()
            seen = set()
            for row in pending_rows:
                run_id = UUID(row["params"]["run_id"])
                if run_id in seen:
                    continue
                seen.add(run_id)
                pending = (row["verdict_details"] or {}).get("approval_request") or (
                    row["raw_output"] or {}
                ).get("approval_request", {})
                now = self.clock()
                reason = None
                try:
                    expires = datetime.fromisoformat(pending["expires_at"])
                    if expires.utcoffset() is None:
                        raise ValueError
                except (KeyError, ValueError, TypeError):
                    reason = "STALE"
                else:
                    if now >= expires:
                        reason = "EXPIRED"
                    else:
                        try:
                            proposal, snap, _ = await load_context(
                                conn, row, now=now, behavior=self.behavior
                            )
                        except ApprovalError as error:
                            if error.code != "STALE_PROPOSAL":
                                raise
                            reason = "STALE"
                        else:
                            if (proposal.payload_hash != pending.get("payload_hash")
                                    or snap.record_versions() != pending.get("record_versions")):
                                reason = "STALE"
                if reason is None:
                    continue
                run = await repos.payout_runs.get(run_id)
                await cas_transition(
                    conn, PROPOSAL, "proposals", row["id"], row["state"], row["version"],
                    ProposalEvent.APPROVAL_EXPIRED,
                )
                await repos.audit_log.insert(
                    actor="system", kind="STATE_TRANSITION", proposal_id=row["id"],
                    details={"event": "APPROVAL_EXPIRED", "state": "EXPIRED", "reason": reason},
                )
                for reservation in await repos.reservations.find(
                    {"proposal_id": row["id"], "state": "ACTIVE", "kind": "PROPOSAL_CASH"}
                ):
                    await release_reservation(conn, reservation["id"])
                await self._release_run(conn, run_id)
                details = {"run_id": str(run_id), "week_start": run["week_start"].isoformat(),
                           "reason": reason}
                try:
                    async with conn.transaction():
                        await self._plan_run(conn, run["week_start"])
                except ApprovalError as error:
                    if error.code not in {"NO_PAYABLE_OBLIGATIONS", "SHIELD_BLOCKED"}:
                        raise
                    details["code"] = error.code
                await repos.audit_log.insert(
                    actor="system", kind="RUN_REQUEST_REFRESHED", proposal_id=row["id"],
                    details=details,
                )
                refreshed += 1
        return refreshed

    async def approve(self, proposal_id: UUID, payload_hash: str, approved_by="owner") -> dict:
        hash_bytes(payload_hash)
        async with self.database.tx() as conn:
            await self._lock(conn)
            repos = Repositories(conn)
            row = await repos.proposals.get(proposal_id, for_update=True)
            if row is None:
                raise ApprovalError("PROPOSAL_NOT_FOUND", 404)
            if row["state"] != "NEEDS_OWNER":
                raise ApprovalError("STALE_PROPOSAL")
            cursor = await conn.execute(
                "SELECT id FROM approvals WHERE proposal_id=%s ORDER BY id FOR UPDATE",
                (proposal_id,),
            )
            if await cursor.fetchone():
                raise ApprovalError("STALE_PROPOSAL")
            pending = (row["verdict_details"] or {}).get("approval_request") or (
                row["raw_output"] or {}
            ).get("approval_request", {})
            try:
                expires = datetime.fromisoformat(pending["expires_at"])
                if expires.utcoffset() is None:
                    raise ValueError
            except (KeyError, ValueError, TypeError):
                raise ApprovalError("STALE_PROPOSAL") from None
            if self.clock() >= expires:
                raise ApprovalError("APPROVAL_EXPIRED")
            proposal, snap, _ = await load_context(conn, row, now=self.clock(), behavior=self.behavior)
            if (
                payload_hash != pending.get("payload_hash")
                or proposal.payload_hash != payload_hash
                or snap.record_versions() != pending.get("record_versions")
            ):
                raise ApprovalError("STALE_PROPOSAL")
            if evaluate(proposal, snap).verdict is Verdict.BLOCK:
                raise ApprovalError("SHIELD_BLOCKED")
            if proposal.action is model.Action.SCHEDULE_PAYOUT_RUN:
                run_id = UUID(row["params"]["run_id"])
                run = await repos.payout_runs.get(run_id, for_update=True)
                if run is None or run["state"] != "AWAITING_OWNER":
                    raise ApprovalError("STALE_PROPOSAL")
                # payout_runs has no C10 machine; guard its documented owner transition.
                await repos.payout_runs.update(
                    run_id, expected_version=run["version"], state="APPROVED"
                )
                for reservation in await repos.reservations.find(
                    {"proposal_id": proposal_id, "state": "ACTIVE", "kind": "PROPOSAL_CASH"}
                ):
                    await release_reservation(conn, reservation["id"])
                    await create_reservation(
                        conn, "RUN_CASH", _money(reservation), run_id=run_id,
                        proposal_id=proposal_id,
                    )
            await cas_transition(
                conn,
                PROPOSAL,
                "proposals",
                proposal_id,
                row["state"],
                row["version"],
                ProposalEvent.OWNER_APPROVED,
            )
            # Re-load after every approval-time state/version bump, using shield's same function.
            proposal, snap, _ = await load_context(conn, row, now=self.clock(), behavior=self.behavior)
            approval = await repos.approvals.insert(
                proposal_id=proposal_id,
                payload_hash=hash_bytes(payload_hash),
                record_versions=snap.record_versions(),
                approved_by=approved_by,
                approved_at=snap.now,
                expires_at=expires,
            )
            if proposal.action is model.Action.SCHEDULE_PAYOUT_RUN:
                await repos.payout_runs.update(
                    UUID(row["params"]["run_id"]), approval_id=approval["id"]
                )
            await repos.audit_log.insert(
                actor="owner",
                kind="OWNER_APPROVED",
                proposal_id=proposal_id,
                details={"approval_id": str(approval["id"])},
            )
            return {
                "id": approval["id"],
                "approved_by": approved_by,
                "approved_at": approval["approved_at"],
                "expires_at": expires,
                "payload_hash": payload_hash,
                "used_at": None,
            }

    async def _release_run(self, conn, run_id):
        repos = Repositories(conn)
        for line in await repos.payout_lines.find({"run_id": run_id}, limit=1000):
            for link in await repos.payout_line_obligations.find(
                {"line_id": line["id"], "active": True}, limit=1000
            ):
                obligation = await repos.obligations.get(link["obligation_id"], for_update=True)
                if obligation["state"] == "SCHEDULED":
                    await cas_transition(
                        conn,
                        OBLIGATION,
                        "obligations",
                        obligation["id"],
                        obligation["state"],
                        obligation["version"],
                        ObligationEvent.LINE_RELEASED,
                    )
                await repos.payout_line_obligations.update(
                    (line["id"], obligation["id"]), active=False
                )
            await cas_transition(
                conn,
                LINE,
                "payout_lines",
                line["id"],
                line["state"],
                line["version"],
                LineEvent.RELEASED,
            )
        run = await repos.payout_runs.get(run_id, for_update=True)
        await repos.payout_runs.update(run_id, expected_version=run["version"], state="CANCELLED")

    async def reject(self, proposal_id: UUID, reason: str) -> dict:
        async with self.database.tx() as conn:
            await self._lock(conn)
            repos = Repositories(conn)
            row = await repos.proposals.get(proposal_id, for_update=True)
            if row is None:
                raise ApprovalError("PROPOSAL_NOT_FOUND", 404)
            if row["state"] != "NEEDS_OWNER":
                raise ApprovalError("STALE_PROPOSAL")
            # Rejection remains possible even if cash evidence has gone missing.
            for table in (
                "makers",
                "orders",
                "obligations",
                "payout_runs",
                "payout_lines",
                "payout_attempts",
                "disputes",
                "operations",
                "reservations",
            ):
                await _rows(conn, table)
            await cas_transition(
                conn,
                PROPOSAL,
                "proposals",
                proposal_id,
                row["state"],
                row["version"],
                ProposalEvent.OWNER_REJECTED,
            )
            for reservation in await repos.reservations.find(
                {"proposal_id": proposal_id, "state": "ACTIVE"}
            ):
                await release_reservation(conn, reservation["id"])
            if row["action"] == "SCHEDULE_PAYOUT_RUN":
                await self._release_run(conn, UUID(row["params"]["run_id"]))
            await repos.audit_log.insert(
                actor="owner",
                kind="OWNER_REJECTED",
                proposal_id=proposal_id,
                details={"reason_provided": bool(reason)},
            )
            return await repos.proposals.get(proposal_id)

    async def set_payout_email(self, maker_id: UUID, payout_email: str) -> dict:
        if len(payout_email) > 254 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", payout_email):
            raise ApprovalError("INVALID_PAYOUT_EMAIL", 422)
        async with self.database.tx() as conn:
            await self._lock(conn)
            repo = Repositories(conn).makers
            row = await repo.get(maker_id, for_update=True)
            if row is None:
                raise ApprovalError("MAKER_NOT_FOUND", 404)
            return await repo.update(
                maker_id,
                expected_version=row["version"],
                payout_email=payout_email,
                payout_email_verified_at=self.clock(),
            )
