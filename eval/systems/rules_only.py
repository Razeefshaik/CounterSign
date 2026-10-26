"""M12 deterministic, mock-only baseline; no Engine, shield, LLM or truth access.

Run all scenarios locally:
    python -m eval.systems.rules_only --k 2 --output <scratch-folder>

The order book comes only from initial_state. Public GETs decide dispute and
payout states; webhooks are wake-up hints and never authorize a payment. Shares
round half up in integer minor units. An owner approves a frozen weekly manifest
including separate replacements of GET-confirmed returned historical payments.
Every intended payment retains one body and cs-<uuid7>-a<n> key on retry. Duplicate
400s are resolved through details[].link, with recipient, amount and item identity
checked before adoption. Unknown attempts stop replaying after 25 mock days.

Text handling is deliberately limited to fixed keywords. It cannot understand
negation, implied intent or nuanced evidence. Refund keywords request the owner;
messages never supply recipients. This is an in-memory baseline, not a durable
executor or a claim of production-grade approval binding/crash recovery. A failed
cancel racing a claim is traced as failed even when GET confirms SUCCESS; the
current grader therefore counts its required cancel action as incomplete.
"""

import argparse
import asyncio
import hashlib
import json
import re
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote, unquote, urljoin, urlsplit
from uuid import UUID

import httpx

from eval.metrics import instant, minor
from eval.systems.base import ActionRecord, OwnerRequest, SystemTrace, require_local

PAYOUTS = "/v1/payments/payouts"
DISPUTES = "/v1/customer/disputes"
RETURNED = {"FAILED", "RETURNED", "REFUNDED", "REVERSED"}


def money(value):
    return {"minor": value, "currency": "USD"}


def decimal(value):
    return {"value": f"{value // 100}.{value % 100:02d}", "currency": "USD"}


@dataclass
class Payment:
    id: str
    maker: str
    orders: tuple[str, ...]
    value: int
    body: dict
    created_at: datetime
    state: str = "PREPARED"
    batch_id: str | None = None
    item_id: str | None = None


class RulesOnlySystem:
    name = "rules_only"

    async def setup(self, env):
        require_local(env.mock_base_url)
        if urlsplit(env.mock_base_url).path not in {"", "/"} or env.scenario["ground"] != "mock":
            raise ValueError("Rules-only evaluation requires the local mock")
        self.env, self.base = env, env.mock_base_url.rstrip("/")
        self.initial = deepcopy(env.scenario["initial_state"])
        self.makers = {m["key"]: m for m in self.initial["makers"]}
        self.orders = {o["key"]: o for o in self.initial["orders"]}
        self.shares = {
            key: (o["amount"]["minor"] * self.initial["policy"].get("maker_share_pct", 90) + 50)
            // 100
            for key, o in self.orders.items()
        }
        self.history = deepcopy(self.initial.get("prior_payouts", []))
        for prior in self.history:
            if "order_keys" not in prior and any(
                o["maker"] == prior["maker"] for o in self.orders.values()
            ):
                raise ValueError("Historical payouts require explicit order links")
        self.trace = SystemTrace([], 0, [], 0, 0, 0)
        self.token = None
        self.payments, self.pending, self.work = {}, {}, {}
        self.history_docs, self.disputes = {}, {}
        self.reserved, self.triaged, self.planned = set(), set(), set()
        self.debits = []

    def now(self):
        return instant(self.env.mock.now)

    def record(self, action, params, outcome="ok", approved=False):
        self.trace.actions.append(
            ActionRecord(self.env.mock.now, action, deepcopy(params), outcome, approved)
        )

    def target(self, href, pattern, *, duplicate=False):
        p, base = urlsplit(urljoin(self.base + "/", href)), urlsplit(self.base)
        local = p.scheme == base.scheme and (p.hostname, p.port or 80) == (
            base.hostname,
            base.port or 80,
        )
        alias = (
            duplicate
            and p.scheme == "https"
            and p.hostname in {"api.sandbox.paypal.com", "api-m.sandbox.paypal.com"}
            and p.port in {None, 443}
        )
        if (
            not (local or alias)
            or p.username
            or p.password
            or p.fragment
            or not re.fullmatch(pattern, unquote(p.path))
            or "\\" in unquote(p.path)
        ):
            raise ValueError("Invalid mock resource link")
        return p.path + ("?" + p.query if p.query else "")

    async def call(self, method, path, **kwargs):
        # Recheck the borrowed transport as well as the advertised origin on every call.
        if str(self.env.mock.client.base_url).rstrip("/") != self.base:
            raise ValueError("Mock transport origin changed")
        self.target(path, r"/v[12]/[A-Za-z0-9/_-]+")
        supplied_headers = kwargs.pop("headers", {})
        for retry in range(2):
            if self.token is None:
                auth = await self.env.mock.client.post(
                    "/v1/oauth2/token",
                    auth=(self.env.paypal_client_id, self.env.paypal_client_secret),
                    data={"grant_type": "client_credentials"},
                    follow_redirects=False,
                )
                if auth.status_code != 200:
                    raise RuntimeError("Mock authentication failed")
                self.token = auth.json()["access_token"]
            response = await self.env.mock.client.request(
                method,
                path,
                headers={"Authorization": "Bearer " + self.token, **supplied_headers},
                follow_redirects=False,
                **kwargs,
            )
            if response.status_code != 401 or retry:
                return response
            self.token = None

    async def get(self, path):
        response = await self.call("GET", path)
        if response.status_code != 200 or not isinstance(response.json(), dict):
            raise RuntimeError("Mock resource read failed")
        return response.json()

    async def sync(self):
        disputes, path, visited = {}, DISPUTES, set()
        while path:
            if path in visited or len(visited) >= 200:
                raise RuntimeError("Invalid dispute pagination")
            visited.add(path)
            page = await self.get(path)
            for item in page["items"]:
                identifier = item["dispute_id"]
                disputes[identifier] = await self.get(DISPUTES + "/" + quote(identifier, safe=""))
            path = next(
                (
                    self.target(link["href"], DISPUTES)
                    for link in page.get("links", [])
                    if link.get("rel") == "next"
                ),
                None,
            )
        reserved = set()
        for doc in disputes.values():
            outcome = doc.get("dispute_outcome") or {}
            released = (
                doc["status"] == "RESOLVED"
                and outcome.get("outcome_code") in {"RESOLVED_SELLER_FAVOUR", "CANCELED_BY_BUYER"}
                and minor(outcome.get("amount_refunded", decimal(0))) == 0
            )
            if not released:
                captures = {t.get("seller_transaction_id") for t in doc["disputed_transactions"]}
                reserved.update(
                    key for key, order in self.orders.items() if order["capture_id"] in captures
                )
        for key in sorted(reserved - self.reserved):
            self.record("RESERVE_EARNINGS", {"order_key": key, "amount": money(self.shares[key])})
        for key in sorted(self.reserved - reserved):
            self.record("RELEASE_RESERVE", {"order_key": key})
        self.reserved, self.disputes = reserved, disputes
        for prior in self.history:
            doc = await self.get(
                "/v1/payments/payouts-item/" + quote(prior["payout_item_id"], safe="")
            )
            if (
                doc["payout_item_id"] != prior["payout_item_id"]
                or minor(doc["payout_item"]["amount"]) != prior["amount"]["minor"]
            ):
                raise RuntimeError("Historical payout does not match")
            if doc["transaction_status"] == "UNCLAIMED":
                doc = await self.cancel(doc)
            self.history_docs[prior["key"]] = doc

    async def cancel(self, doc):
        path = "/v1/payments/payouts-item/" + quote(doc["payout_item_id"], safe="")
        try:
            response = await self.call("POST", path + "/cancel")
            outcome = "ok" if response.status_code == 200 else "failed"
        except (httpx.HTTPError, ConnectionError):
            outcome = "unknown"
        observed = await self.get(path)
        self.record(
            "CANCEL_UNCLAIMED_PAYOUT",
            {
                "payout_item_id": doc["payout_item_id"],
                "observed_status": observed["transaction_status"],
            },
            outcome,
        )
        return observed

    def lines(self, week):
        date.fromisoformat(week)
        occupied = {key for h in self.history for key in h.get("order_keys", [])}
        occupied.update(key for p in self.payments.values() for key in p.orders)
        lines = []
        for maker in sorted(self.makers):
            keys = sorted(
                k
                for k, o in self.orders.items()
                if o["maker"] == maker and k not in occupied and k not in self.reserved
            )
            value = sum(self.shares[k] for k in keys)
            if value and self.makers[maker]["verified"]:
                lines.append(
                    {
                        "id": "orders:" + ",".join(keys),
                        "maker": maker,
                        "order_keys": keys,
                        "amount": money(value),
                    }
                )
        for prior in sorted(self.history, key=lambda p: p["key"]):
            identity = "prior:" + prior["key"]
            if (
                identity not in self.payments
                and self.history_docs[prior["key"]]["transaction_status"] in RETURNED
                and not self.reserved.intersection(prior.get("order_keys", []))
                and self.makers[prior["maker"]]["verified"]
            ):
                lines.append(
                    {
                        "id": identity,
                        "maker": prior["maker"],
                        "order_keys": prior.get("order_keys", []),
                        "amount": deepcopy(prior["amount"]),
                        "replaces": prior["payout_item_id"],
                    }
                )
        return lines

    def request_owner(self, action, params, total, work):
        identifier = hashlib.sha256(
            json.dumps([action, params, total], sort_keys=True).encode()
        ).hexdigest()
        if identifier not in self.work:
            self.pending[identifier] = OwnerRequest(
                identifier, action, deepcopy(params), total, "Fixed rules-only workflow"
            )
            self.work[identifier] = deepcopy(work)
            self.trace.owner_requests += 1

    async def triage(self):
        for identifier, doc in sorted(self.disputes.items()):
            if doc["status"] == "RESOLVED":
                continue
            text = " ".join(
                m.get("content", "")
                for m in doc.get("messages", [])
                if isinstance(m.get("content"), str)
            ).lower()
            if re.search(r"\b(refund|damaged|broken|crack|cracked)\b", text):
                self.request_owner(
                    "ACCEPT_CLAIM",
                    {"paypal_dispute_id": identifier},
                    money(minor(doc["dispute_amount"])),
                    {"kind": "refund"},
                )
                continue
            key = (identifier, doc["status"], bool(doc.get("evidences")))
            if key in self.triaged:
                continue
            self.triaged.add(key)
            reason = doc.get("reason", "").lower().replace("_", " ")
            missing = any(
                word in text + " " + reason
                for word in ["not received", "not arrived", "never arrived", "missing parcel"]
            )
            captures = {t.get("seller_transaction_id") for t in doc["disputed_transactions"]}
            tracking = [
                o["tracking"]
                for o in self.orders.values()
                if o["capture_id"] in captures and o.get("tracking")
            ]
            for capture in sorted(c for c in captures if isinstance(c, str)):
                tracking.extend(
                    (
                        await self.get(
                            "/v1/shipping/trackers?transaction_id=" + quote(capture, safe="")
                        )
                    ).get("trackers", [])
                )
            permitted = any(
                link.get("rel") == "provide_evidence" and link.get("method") == "POST"
                for link in doc.get("links", [])
            )
            if (
                missing
                and tracking
                and permitted
                and doc["status"] == "WAITING_FOR_SELLER_RESPONSE"
            ):
                response = await self.call(
                    "POST",
                    DISPUTES + "/" + quote(identifier, safe="") + "/provide-evidence",
                    json={
                        "evidences": [
                            {
                                "evidence_type": "PROOF_OF_FULFILLMENT",
                                "notes": "Merchant-supplied tracking is available; it is not carrier-confirmed delivery proof.",
                            }
                        ]
                    },
                )
                self.record(
                    "PROVIDE_EVIDENCE",
                    {"paypal_dispute_id": identifier},
                    "ok" if response.status_code == 200 else "failed",
                )
            else:
                self.record(
                    "WAIT",
                    {
                        "paypal_dispute_id": identifier,
                        "reason": "No fixed keyword/evidence rule permits action",
                    },
                )

    async def on_step(self, step, env):
        await self.sync()
        if step["do"] == "plan_payout_run":
            week = step["week_start"]
            lines = self.lines(week)
            if lines and week not in self.planned:
                self.planned.add(week)
                params = {"week_start": week, "lines": lines}
                self.request_owner(
                    "SCHEDULE_PAYOUT_RUN",
                    params,
                    money(sum(l["amount"]["minor"] for l in lines)),
                    {"kind": "run", "params": params},
                )
        else:
            await self.triage()
        for payment in self.payments.values():
            await self.progress(payment)

    async def on_webhook(self, headers, raw_body):
        # Intentionally ignore even signed payload claims. The next tick polls GETs.
        pass

    async def pending_owner_requests(self):
        return deepcopy(list(self.pending.values()))

    async def owner_decide(self, request_id, approve):
        request = self.pending.pop(request_id)
        work = self.work[request_id]
        if not approve:
            self.record(request.action, request.params, "blocked")
            return
        await self.sync()
        if work["kind"] == "run":
            if self.lines(request.params["week_start"]) != request.params["lines"]:
                self.record(request.action, request.params, "blocked", True)
                return
            balance = await self.get("/v1/reporting/balances")
            usd = next(b for b in balance["balances"] if b["currency"] == "USD")
            refresh = instant(balance["last_refresh_time"])
            available = minor(usd["available_balance"]) - sum(
                value for at, value in self.debits if at >= refresh
            )
            if self.now() - refresh > timedelta(hours=3) or request.total["minor"] > available:
                self.record(request.action, request.params, "blocked", True)
                return
            self.record(request.action, request.params, approved=True)
            for line in request.params["lines"]:
                payment = self.prepare(line)
                self.payments[payment.id] = payment
                await self.progress(payment)
        elif work["kind"] == "refund":
            identifier = request.params["paypal_dispute_id"]
            doc = self.disputes.get(identifier)
            if (
                not doc
                or doc["status"] == "RESOLVED"
                or minor(doc["dispute_amount"]) != request.total["minor"]
            ):
                self.record(request.action, request.params, "blocked", True)
                return
            try:
                response = await self.call(
                    "POST",
                    DISPUTES + "/" + quote(identifier, safe="") + "/accept-claim",
                    json={
                        "accept_claim_type": "REFUND",
                        "note": "Owner approved the exact rules-only refund",
                    },
                )
                outcome = (
                    "ok"
                    if response.status_code == 200
                    else "unknown"
                    if response.status_code >= 500
                    else "failed"
                )
            except (httpx.HTTPError, ConnectionError):
                outcome = "unknown"
            self.record(
                request.action,
                request.params,
                outcome,
                True,
            )
            if outcome in {"ok", "unknown"}:
                self.debits.append((self.now(), request.total["minor"]))
        else:
            self.record("REQUEST_OWNER", request.params, "blocked", True)

    def prepare(self, line):
        context = json.dumps([self.env.run_id, self.env.k_index, line], sort_keys=True)
        entropy = int(hashlib.sha256(context.encode()).hexdigest(), 16)
        ms = (self.now() - datetime(1970, 1, 1, tzinfo=UTC)) // timedelta(milliseconds=1)
        identifier = str(
            UUID(
                int=(ms << 80)
                | (7 << 76)
                | ((entropy & ((1 << 12) - 1)) << 64)
                | (2 << 62)
                | ((entropy >> 12) & ((1 << 62) - 1))
            )
        )
        key = "cs-" + identifier + ("-a2" if "replaces" in line else "-a1")
        payload = {
            "sender_batch_header": {"sender_batch_id": key},
            "items": [
                {
                    "recipient_type": "EMAIL",
                    "receiver": self.makers[line["maker"]]["payout_email"],
                    "amount": decimal(line["amount"]["minor"]),
                    "sender_item_id": key + "-i1",
                }
            ],
        }
        return Payment(
            line["id"],
            line["maker"],
            tuple(line["order_keys"]),
            line["amount"]["minor"],
            payload,
            self.now(),
        )

    async def progress(self, payment):
        if payment.state in {"PREPARED", "UNKNOWN"}:
            if self.reserved.intersection(payment.orders) and payment.state == "PREPARED":
                payment.state = "BLOCKED"
                return
            if self.now() - payment.created_at >= timedelta(days=25):
                self.request_owner(
                    "REQUEST_OWNER",
                    {"payment_id": payment.id, "reason": "Replay window expired"},
                    money(payment.value),
                    {"kind": "unknown"},
                )
                return
            if payment.state == "PREPARED":
                self.debits.append((self.now(), payment.value))
            payment.state = "UNKNOWN"
            key = payment.body["sender_batch_header"]["sender_batch_id"]
            try:
                response = await self.call(
                    "POST", PAYOUTS, json=deepcopy(payment.body), headers={"PayPal-Request-Id": key}
                )
            except (httpx.HTTPError, ConnectionError):
                self.record("SCHEDULE_PAYOUT_RUN", {"payment_id": payment.id}, "unknown", True)
                return
            if response.status_code == 201:
                batch = response.json()["batch_header"]["payout_batch_id"]
                payment.batch_id = batch
            elif (
                response.status_code == 400 and response.json().get("name") == "USER_BUSINESS_ERROR"
            ):
                links = [
                    link
                    for detail in response.json().get("details", [])
                    for link in detail.get("link", [])
                ]
                link = next(
                    (
                        link
                        for link in links
                        if link.get("rel") == "self" and link.get("method") == "GET"
                    ),
                    None,
                )
                if not link:
                    return
                path = self.target(link["href"], PAYOUTS + r"/[A-Za-z0-9_-]+", duplicate=True)
                if urlsplit(path).query:
                    raise ValueError("Invalid original batch link")
                payment.batch_id = path.rsplit("/", 1)[-1]
            elif (
                response.status_code >= 500
                or response.status_code in {408, 409}
                or response.json().get("name") == "PREVIOUS_REQUEST_IN_PROGRESS"
            ):
                return
            else:
                payment.state = "REJECTED"
                self.record("SCHEDULE_PAYOUT_RUN", {"payment_id": payment.id}, "failed", True)
                return
        if payment.batch_id:
            doc = await self.get(PAYOUTS + "/" + quote(payment.batch_id, safe=""))
            items = doc.get("items", [])
            if (
                doc["batch_header"]["payout_batch_id"] != payment.batch_id
                or doc["batch_header"]["sender_batch_header"]["sender_batch_id"]
                != payment.body["sender_batch_header"]["sender_batch_id"]
                or len(items) != 1
                or items[0]["payout_batch_id"] != payment.batch_id
                or any(
                    items[0]["payout_item"].get(k) != v for k, v in payment.body["items"][0].items()
                )
            ):
                raise RuntimeError("Original batch does not match the payment")
            payment.item_id = items[0]["payout_item_id"]
            observed = await self.get(
                "/v1/payments/payouts-item/" + quote(payment.item_id, safe="")
            )
            if (
                observed["payout_item_id"] != payment.item_id
                or observed["payout_batch_id"] != payment.batch_id
                or any(
                    observed["payout_item"].get(k) != v for k, v in payment.body["items"][0].items()
                )
            ):
                raise RuntimeError("Original item does not match the payment")
            if observed["transaction_status"] == "UNCLAIMED":
                observed = await self.cancel(observed)
            payment.state = observed["transaction_status"]

    async def teardown(self):
        return deepcopy(self.trace)


def main():
    from eval.runner import run_suite

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--k", type=int, default=2)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1] / "scenarios"
    scenarios = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(root.glob("*.json"))]
    rows = asyncio.run(
        run_suite(scenarios, [RulesOnlySystem], output=args.output, k=args.k, model="none")
    )
    print(f"rules_only: {sum(row['passed'] for row in rows)}/{len(rows)} passed (mock; no LLM)")


if __name__ == "__main__":
    main()
