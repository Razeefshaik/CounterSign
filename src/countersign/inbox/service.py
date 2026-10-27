"""A PostgreSQL inbox: acknowledgement records bytes; a worker verifies and reads.

The unique event id makes delivery idempotent. UNKNOWN verification is retried,
not rejected. Worker transactions hold row locks through confirmation so a failed
earmark, CAS or commit rolls the complete financial update back.
"""

import json
from datetime import UTC, datetime

from psycopg.types.json import Jsonb

from countersign.db import Repositories, cas_transition
from countersign.fsm.dispute import Phase, phase
from countersign.fsm.obligation import OBLIGATION, ObligationEvent
from countersign.ledger.journal import serialize_on
from countersign.ledger.reservations import earmark_dispute_share
from countersign.money import Money
from countersign.paypal.webhook_verify import Verification

SIGNATURE_HEADERS = frozenset(
    {
        "paypal-auth-algo",
        "paypal-cert-url",
        "paypal-transmission-id",
        "paypal-transmission-sig",
        "paypal-transmission-time",
    }
)


class InboxError(RuntimeError):
    """Safe failure text; never includes event contents or upstream messages."""


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate event key")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError("Invalid JSON constant")


def _date(value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("Timestamp must be a string")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Timestamp must include its timezone")
    return parsed


class Inbox:
    def __init__(self, database, reader, verifier, *, capture_field="seller_transaction_id"):
        self.database = database
        self.reader = reader
        self.verifier = verifier
        # [pending D8b]: selectable default, never a guessed alternate id field.
        self.capture_field = capture_field

    async def receive(self, raw_body: bytes, headers) -> bool:
        """Return only after commit. False means this id was already durably stored."""
        try:
            payload = json.loads(
                raw_body, object_pairs_hook=_object, parse_constant=_invalid_constant
            )
            if not isinstance(payload, dict) or not all(
                isinstance(payload.get(k), str) and payload[k] for k in ("id", "event_type")
            ):
                raise ValueError
            resource = payload.get("resource", {})
            if not isinstance(resource, dict):
                raise TypeError
            batch_header = resource.get("batch_header", {})
            if not isinstance(batch_header, dict):
                raise TypeError
            resource_id = (
                resource.get("dispute_id")
                or resource.get("payout_item_id")
                or resource.get("payout_batch_id")
                or batch_header.get("payout_batch_id")
                or resource.get("id")
            )
            if resource_id is not None and not isinstance(resource_id, str):
                raise TypeError
            event_time = _date(payload.get("create_time"))
        except (ValueError, TypeError, UnicodeError, AttributeError):
            raise InboxError("Invalid webhook event") from None
        selected = {k.lower(): v for k, v in headers.items() if k.lower() in SIGNATURE_HEADERS}
        async with self.database.tx() as conn:
            cursor = await conn.execute(
                "INSERT INTO webhook_inbox(event_id,event_type,resource_type,resource_id,"
                "headers,raw_body,payload,paypal_create_time,signature,status) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,'UNVERIFIED','RECEIVED') "
                "ON CONFLICT(event_id) DO NOTHING RETURNING event_id",
                (
                    payload["id"],
                    payload["event_type"],
                    payload.get("resource_type"),
                    resource_id,
                    Jsonb(selected),
                    raw_body,
                    Jsonb(payload),
                    event_time,
                ),
            )
            inserted = await cursor.fetchone() is not None
        return inserted

    async def process_one(self) -> bool:
        """Claim one pending delivery. An error never acknowledges financial changes."""
        event_id = None
        try:
            async with self.database.tx() as conn:
                await serialize_on(conn, "countersign:owner-decisions")
                cursor = await conn.execute(
                    "SELECT * FROM webhook_inbox WHERE status IN ('RECEIVED','ERROR') "
                    "ORDER BY attempts,received_at,event_id FOR UPDATE SKIP LOCKED LIMIT 1"
                )
                event = await cursor.fetchone()
                if event is None:
                    return False
                event_id = event["event_id"]
                repos = Repositories(conn)
                signature = event["signature"]
                if signature != Verification.SUCCESS:
                    signature = await self.verifier.verify(
                        bytes(event["raw_body"]), event["headers"]
                    )
                common = {"attempts": event["attempts"] + 1, "error": None}
                if signature not in {Verification.SUCCESS, Verification.FAILURE}:
                    await repos.webhook_inbox.update(
                        event_id, signature="UNVERIFIED", status="RECEIVED", **common
                    )
                    return True
                if signature != Verification.SUCCESS:
                    await repos.webhook_inbox.update(
                        event_id,
                        signature="FAILURE",
                        status="REJECTED",
                        processed_at=datetime.now(UTC),
                        **common,
                    )
                    return True
                handled = await self._process(conn, event)
                await repos.webhook_inbox.update(
                    event_id,
                    signature="SUCCESS",
                    status="PROCESSED" if handled else "IGNORED",
                    processed_at=datetime.now(UTC),
                    **common,
                )
            return True
        except Exception:  # noqa: BLE001 - roll back all domain/driver failures before retry.
            if event_id is None:
                raise InboxError("Webhook processing unavailable") from None
            # The first transaction has rolled back. Persist a safe retry marker only.
            async with self.database.tx() as conn:
                await conn.execute(
                    "UPDATE webhook_inbox SET status='ERROR',attempts=attempts+1,"
                    "error='Resource confirmation or atomic update failed' "
                    "WHERE event_id=%s AND status IN ('RECEIVED','ERROR')",
                    (event_id,),
                )
            return True

    async def _process(self, conn, event):
        event_type, resource_id = event["event_type"], event["resource_id"]
        if not resource_id:
            raise InboxError("Webhook resource id missing")
        if event_type.startswith("CUSTOMER.DISPUTE."):
            response = await self.reader.get_dispute(resource_id)
            if not 200 <= response.status_code < 300:
                raise InboxError("Dispute confirmation unavailable")
            detail = response.body
            if not isinstance(detail, dict) or detail.get("dispute_id") != resource_id:
                raise InboxError("Dispute identity mismatch")
            await self._dispute(conn, detail)
            return True
        if event_type.startswith("PAYMENT.PAYOUTS-ITEM."):
            response = await self.reader.get_payout_item(resource_id)
        elif event_type.startswith("PAYMENT.PAYOUTSBATCH."):
            response = await self.reader.get_payout_batch(resource_id)
        else:
            return False
        if not 200 <= response.status_code < 300:
            raise InboxError("Payout confirmation unavailable")
        detail = response.body
        if not isinstance(detail, dict):
            raise InboxError("Payout identity mismatch")
        if event_type.startswith("PAYMENT.PAYOUTS-ITEM."):
            confirmed_id = detail.get("payout_item_id")
        else:
            header = detail.get("batch_header")
            confirmed_id = header.get("payout_batch_id") if isinstance(header, dict) else None
        if confirmed_id != resource_id:
            raise InboxError("Payout identity mismatch")
        # C15 owns payout reconciliation and posting. Delivery itself changes no payout state.
        return True

    async def _dispute(self, conn, detail):
        repos = Repositories(conn)
        captures = {
            entry.get(self.capture_field) for entry in detail.get("disputed_transactions", [])
        }
        orders = []
        for capture in captures - {None}:
            orders.extend(await repos.orders.find({"paypal_capture_id": capture}))
        if len(orders) != 1:
            raise InboxError("Dispute capture cannot be uniquely matched")
        order = orders[0]
        amount = Money.from_paypal(detail["dispute_amount"])
        if amount.currency != order["currency"].strip():
            raise InboxError("Dispute currency mismatch")
        outcome = detail.get("dispute_outcome") or {}
        refunded = (
            Money.from_paypal(outcome["amount_refunded"])
            if outcome.get("amount_refunded")
            else None
        )
        if refunded and refunded.currency != amount.currency:
            raise InboxError("Refund currency mismatch")
        derived = phase(
            detail["status"],
            outcome.get("outcome_code"),
            refunded.minor if refunded else None,
            amount.minor,
        )
        fields = {
            "order_id": order["id"],
            "status": detail["status"],
            "lifecycle_stage": detail.get("dispute_life_cycle_stage"),
            "reason": detail.get("reason"),
            "amount_minor": amount.minor,
            "currency": amount.currency,
            "seller_response_due_date": _date(detail.get("seller_response_due_date")),
            "outcome_code": outcome.get("outcome_code"),
            "amount_refunded_minor": refunded.minor if refunded else None,
            "phase": derived,
            "links": detail.get("links", []),
            "raw": detail,
            "last_synced_at": datetime.now(UTC),
        }
        existing = await repos.disputes.find({"paypal_dispute_id": detail["dispute_id"]})
        if existing and existing[0]["order_id"] != order["id"]:
            raise InboxError("Dispute order changed")
        dispute = (
            await repos.disputes.update(existing[0]["id"], **fields)
            if existing
            else await repos.disputes.insert(paypal_dispute_id=detail["dispute_id"], **fields)
        )
        if derived not in {Phase.ACTIVE, Phase.UNKNOWN_OUTCOME}:
            return  # Reconciliation owns any release or financial resolution.
        policy = await repos.policies.get("maker_share_pct")
        if policy is None or type(policy["value"]) is not int:
            raise InboxError("Maker share policy missing")
        cursor = await conn.execute(
            "SELECT * FROM obligations WHERE order_id=%s AND kind='ORDER_SHARE' "
            "ORDER BY id FOR UPDATE",
            (order["id"],),
        )
        obligations = await cursor.fetchall()
        for obligation in obligations:
            if obligation["state"] in {"OPEN", "SCHEDULED", "RESERVED"}:
                await earmark_dispute_share(
                    conn,
                    dispute_id=dispute["id"],
                    obligation_id=obligation["id"],
                    order_amount=Money(order["amount_minor"], order["currency"].strip()),
                    maker_share_pct=policy["value"],
                )
                if obligation["state"] != "RESERVED":
                    await cas_transition(
                        conn,
                        OBLIGATION,
                        "obligations",
                        obligation["id"],
                        obligation["state"],
                        obligation["version"],
                        ObligationEvent.DISPUTE_OPENED,
                    )
        # Every known dispute has an investigation record, including paid-before-dispute
        # and unknown resolution. No incident is closed by a webhook.
        subject = {"dispute_id": str(dispute["id"])}
        # Discovery enriches subjects with order/maker/PayPal IDs. Identity is the
        # dispute ID, never equality of the entire JSON document. process_one()
        # holds the same owner-decision lock as discovery across lookup + insert.
        # Leave resolved history alone: discovery decides whether changed GET
        # facts justify opening a new investigation.
        cursor = await conn.execute(
            "SELECT id FROM incidents WHERE kind='DISPUTE' AND subject @> %s LIMIT 1",
            (Jsonb(subject),),
        )
        if await cursor.fetchone() is None:
            await repos.incidents.insert(kind="DISPUTE", subject=subject, state="OPEN")
