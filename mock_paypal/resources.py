"""Local resource fixtures; no calls to PayPal. All refreshes use mock time."""

import base64
import json
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from email import policy
from email.parser import BytesParser
from urllib.parse import urlencode

from fastapi import APIRouter, Query, Request

from mock_paypal.clock import timestamp
from mock_paypal.config import MockConfig
from mock_paypal.payout_models import amount, minor
from mock_paypal.payouts import PayoutBatch, PayoutError, PayoutItem, PayoutState

router = APIRouter()


def instant(value):
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError("Timezone required")
    return result


def money(value):
    sign = "-" if value < 0 else ""
    return {"value": sign + amount(abs(value))["value"], "currency_code": "USD"}


@dataclass(frozen=True)
class ResourceBehavior:
    capture_transaction_field: str = "seller_transaction_id"  # pending D8b
    initial_status: str = "WAITING_FOR_SELLER_RESPONSE"
    initial_stage: str = "INQUIRY"
    evidence_result_status: str = "UNDER_REVIEW"
    escalation_status: str = "UNDER_REVIEW"  # pending D10
    seller_win_code: str = "RESOLVED_SELLER_FAVOUR"
    buyer_win_code: str = "RESOLVED_BUYER_FAVOUR"
    buyer_close_code: str = "CANCELED_BY_BUYER"
    refund_uses_withheld: bool = True
    capture_held_status: str = "COMPLETED"  # pending D8c
    action_rels: tuple = (
        "provide_evidence",
        "accept_claim",
        "escalate",
        "adjudicate",
        "require_evidence",
    )


@dataclass
class ResourceState:
    orders: dict = field(default_factory=dict)
    captures: dict = field(default_factory=dict)
    trackers: list = field(default_factory=list)
    disputes: dict = field(default_factory=dict)
    held: dict = field(default_factory=dict)
    snapshots: list = field(default_factory=list)
    transactions: list = field(default_factory=list)
    repeat_transaction_ids: bool = False
    snapshot_generation: int = 0


class ResourceService:
    def __init__(self, store, payouts, behavior):
        self.store, self.payouts, self.behavior = store, payouts, behavior

    def transaction(self, value, id=None):
        state = self.store.resources
        state.transactions.append(
            {
                "transaction_info": {
                    "transaction_id": "MOCKT-REPEATED"
                    if state.repeat_transaction_ids
                    else id or self.store.new_id("MOCKT"),
                    "transaction_event_code": "T0000",
                    "transaction_initiation_date": timestamp(self.store.clock.now),
                    "transaction_amount": money(value),
                    "transaction_status": "S",
                }
            }
        )

    def snapshot(self):
        funds = self.store.payouts
        now = timestamp(self.store.clock.now)
        self.store.resources.snapshots.append(
            {
                "balances": [
                    {
                        "currency": "USD",
                        "primary": True,
                        "total_balance": money(
                            funds.available_minor + funds.withheld_minor
                        ),
                        "available_balance": money(funds.available_minor),
                        "withheld_balance": money(funds.withheld_minor),
                    }
                ],
                "account_id": "MOCKACCT01",
                "as_of_time": now,
                "last_refresh_time": now,
            }
        )

    def schedule_snapshots(self):
        state = self.store.resources
        state.snapshot_generation += 1
        generation = state.snapshot_generation

        def refresh():
            if (
                state is not self.store.resources
                or generation != state.snapshot_generation
            ):
                return
            self.snapshot()
            self.store.clock.schedule(
                self.store.config.balance_refresh_seconds, refresh
            )

        self.store.clock.schedule(self.store.config.balance_refresh_seconds, refresh)

    def get(self, resource, id):
        try:
            return getattr(self.store.resources, resource)[id]
        except KeyError:
            raise PayoutError(404, "RESOURCE_NOT_FOUND", "Resource not found") from None

    def seed(self, body):
        # Stage in a separate state; invalid input leaves live resources untouched.
        state, funds = ResourceState(), PayoutState()
        config = MockConfig.model_validate(
            {**self.store.config.model_dump(), **body.get("config", {})}
        )
        balances = body.get("balances", {})
        if balances.get("currency_code", "USD") != "USD":
            raise ValueError("Only USD")
        funds.available_minor = minor(balances.get("available", "0.00"))
        funds.withheld_minor = minor(balances.get("withheld", "0.00"))
        funds.registered_receivers = set(body.get("registered_receivers", []))
        state.repeat_transaction_ids = body.get("repeat_transaction_ids", False)
        for entry in body.get("orders", []):
            state.orders[entry["id"]] = deepcopy(entry)
        for entry in body.get("captures", []):
            value = (
                entry["amount"]
                if isinstance(entry["amount"], str)
                else entry["amount"]["value"]
            )
            value = minor(value)
            created = entry.get("create_time", timestamp(self.store.clock.now))
            instant(created)
            capture = {
                "id": entry["id"],
                "status": entry.get("status", "COMPLETED"),
                "amount": money(value),
                "seller_receivable_breakdown": {
                    "gross_amount": money(value),
                    "paypal_fee": money(0),
                    "net_amount": money(value),
                },
                "create_time": created,
            }
            state.captures[entry["id"]] = capture
            oid = entry.get("order_id", entry["id"].replace("CAP", "ORD"))
            state.orders.setdefault(
                oid,
                {
                    "id": oid,
                    "status": "COMPLETED",
                    "purchase_units": [
                        {
                            "reference_id": entry.get("reference_id", oid),
                            "amount": money(value),
                            "payments": {
                                "captures": [
                                    {
                                        "id": entry["id"],
                                        "status": capture["status"],
                                        "amount": money(value),
                                    }
                                ]
                            },
                        }
                    ],
                },
            )
            state.transactions.append(
                {
                    "transaction_info": {
                        "transaction_id": "MOCKT-REPEATED"
                        if state.repeat_transaction_ids
                        else entry["id"],
                        "transaction_event_code": "T0000",
                        "transaction_initiation_date": created,
                        "transaction_amount": money(value),
                        "transaction_status": "S",
                    }
                }
            )
        state.trackers = deepcopy(body.get("trackers", []))
        for index, entry in enumerate(body.get("payout_items", [])):
            value = (
                entry["amount"]
                if isinstance(entry["amount"], str)
                else entry["amount"]["value"]
            )
            value = minor(value)
            if value <= 0 or entry.get("status", "PENDING") not in {
                "PENDING",
                "SUCCESS",
                "UNCLAIMED",
                "ONHOLD",
                "BLOCKED",
                "FAILED",
                "RETURNED",
                "REFUNDED",
                "REVERSED",
            }:
                raise ValueError("Invalid seeded payout")
            created = (
                instant(entry["created_at"])
                if entry.get("created_at")
                else self.store.clock.now
            )
            bid = entry.get("payout_batch_id", f"MOCKB-SEED-{index}")
            batch = funds.batches.setdefault(
                bid,
                PayoutBatch(
                    bid,
                    {"sender_batch_id": entry.get("sender_batch_id", bid)},
                    [],
                    created,
                    request_id=entry.get("request_id"),
                ),
            )
            item = PayoutItem(
                entry.get("payout_item_id", f"MOCKI-SEED-{index}"),
                bid,
                {
                    "recipient_type": "EMAIL",
                    "receiver": entry["receiver"],
                    "sender_item_id": entry.get("sender_item_id", f"seed-{index}"),
                    "amount": amount(value),
                },
                created,
                f"MOCKT-SEED-{index}",
                f"MOCKACT-SEED-{index}",
                status=entry.get("status", "PENDING"),
                synthesised=entry.get("synthesised", False),
            )
            item.debited_minor = (
                value
                if item.status
                in {"PENDING", "SUCCESS", "UNCLAIMED", "ONHOLD", "BLOCKED"}
                else 0
            )
            if item.status == "UNCLAIMED" and config.unclaimed_counts_in_available:
                item.debited_minor = 0
            if item.id in funds.items:
                raise ValueError("Duplicate item")
            funds.items[item.id] = item
            batch.item_ids.append(item.id)
        # Raw dispute fixtures must use the same PayPal shape as GET.
        for doc in body.get("disputes", []):
            if not all(
                k in doc
                for k in [
                    "dispute_id",
                    "status",
                    "dispute_life_cycle_stage",
                    "dispute_amount",
                    "create_time",
                    "disputed_transactions",
                ]
            ):
                raise ValueError("Invalid dispute fixture")
            state.disputes[doc["dispute_id"]] = deepcopy(doc)
            if doc.get("hold_observed") and doc["status"] != "RESOLVED":
                state.held[doc["dispute_id"]] = minor(doc["dispute_amount"]["value"])
        self.store.config, self.store.resources, self.store.payouts = (
            config,
            state,
            funds,
        )
        for item in funds.items.values():
            if item.status == "UNCLAIMED":
                due = max(
                    0,
                    int(
                        (
                            item.created_at + timedelta(days=30) - self.store.clock.now
                        ).total_seconds()
                    ),
                )
                self.store.clock.schedule(
                    due,
                    lambda item=item: (
                        self.payouts.transition(item, "RETURNED")
                        if self.store.payouts is funds and item.status == "UNCLAIMED"
                        else None
                    ),
                )
            elif item.status == "PENDING":
                self.store.clock.schedule(
                    config.payout_processing_seconds,
                    lambda item=item: (
                        self.payouts.transition(
                            item,
                            "SUCCESS"
                            if item.payload["receiver"] in funds.registered_receivers
                            else "UNCLAIMED",
                        )
                        if self.store.payouts is funds and item.status == "PENDING"
                        else None
                    ),
                )
        for capture in state.captures.values():
            self.store.emit("PAYMENT.CAPTURE.COMPLETED", capture)
        self.snapshot()
        self.schedule_snapshots()

    def links(self, doc, base):
        status, stage = doc["status"], doc["dispute_life_cycle_stage"]
        names = []
        if status != "RESOLVED":
            names += ["accept-claim"]
            if stage == "INQUIRY":
                names += ["escalate"]
        if (
            status == "WAITING_FOR_SELLER_RESPONSE"
            and stage in self.store.config.evidence_allowed_stages
        ):
            names += ["provide-evidence"]
        if status == "UNDER_REVIEW":
            names += ["adjudicate", "require-evidence"]
        mapping = dict(
            zip(
                [
                    "provide-evidence",
                    "accept-claim",
                    "escalate",
                    "adjudicate",
                    "require-evidence",
                ],
                self.behavior.action_rels,
            )
        )
        path = base.rstrip("/") + "/v1/customer/disputes/" + doc["dispute_id"]
        return [{"href": path, "rel": "self", "method": "GET"}] + [
            {"href": path + "/" + name, "rel": mapping[name], "method": "POST"}
            for name in names
        ]

    def doc(self, doc, base):
        return {**deepcopy(doc), "links": self.links(doc, base)}

    def new_dispute(self, body):
        capture = self.get("captures", body["capture_id"])
        value = minor(body["amount"])
        if value <= 0 or value > minor(capture["amount"]["value"]):
            raise ValueError("Invalid dispute amount")
        if not isinstance(body["reason"], str) or not all(
            isinstance(m, str) for m in body.get("messages", [])
        ):
            raise ValueError("Invalid dispute text")
        id, now = self.store.new_id("PP-D-"), timestamp(self.store.clock.now)
        doc = {
            "dispute_id": id,
            "create_time": now,
            "update_time": now,
            "disputed_transactions": [
                {
                    self.behavior.capture_transaction_field: capture["id"],
                    "buyer_transaction_id": self.store.new_id("MOCKBUY"),
                    "create_time": capture["create_time"],
                    "transaction_status": capture["status"],
                    "gross_amount": capture["amount"],
                }
            ],
            "reason": body["reason"],
            "status": self.behavior.initial_status,
            "dispute_amount": money(value),
            "seller_response_due_date": timestamp(
                self.store.clock.now + timedelta(days=10)
            ),
            "dispute_life_cycle_stage": self.behavior.initial_stage,
            "dispute_channel": "INTERNAL",
            "messages": [
                {"posted_by": "BUYER", "time_posted": now, "content": m}
                for m in body.get("messages", [])
            ],
            "evidences": [],
            "dispute_outcome": None,
        }
        self.store.resources.disputes[id] = doc
        if self.store.config.dispute_hold:
            self.store.payouts.available_minor -= value
            self.store.payouts.withheld_minor += value
            self.store.resources.held[id] = value
            capture["status"] = self.behavior.capture_held_status
        self.store.emit(
            "CUSTOMER.DISPUTE.CREATED", self.doc(doc, "http://localhost:8099")
        )
        return doc

    def resolve(self, doc, outcome, refund=False, via="adjudicate"):
        id, value = doc["dispute_id"], minor(doc["dispute_amount"]["value"])
        held = self.store.resources.held.get(id, 0)
        from_held = (
            min(held, value) if refund and self.behavior.refund_uses_withheld else 0
        )
        needed = value - from_held if refund else 0
        if needed > 0 and self.store.payouts.available_minor < needed:
            raise PayoutError(422, "INSUFFICIENT_FUNDS", "Insufficient refund funds")
        self.store.payouts.withheld_minor -= held
        self.store.payouts.available_minor += held - from_held - needed
        self.store.resources.held.pop(id, None)
        doc.update(
            status="RESOLVED",
            dispute_outcome={
                "outcome_code": outcome,
                "amount_refunded": money(value if refund else 0),
            },
        )
        if refund:
            self.store.refunds.append(
                {
                    "dispute_id": id,
                    "amount": money(value),
                    "via": via,
                    "at": timestamp(self.store.clock.now),
                }
            )
            self.transaction(-value, id)

    def updated(self, doc):
        doc["update_time"] = timestamp(self.store.clock.now)
        suffix = "RESOLVED" if doc["status"] == "RESOLVED" else "UPDATED"
        self.store.emit(
            "CUSTOMER.DISPUTE." + suffix, self.doc(doc, "http://localhost:8099")
        )

    async def act(self, id, action, request):
        doc = self.get("disputes", id)
        allowed = [
            link["href"].rsplit("/", 1)[-1]
            for link in self.links(doc, str(request.base_url))
            if link["method"] == "POST"
        ]
        if action not in allowed:
            raise PayoutError(
                422,
                "UNPROCESSABLE_ENTITY",
                "Action not allowed",
                details=[{"issue": "ACTION_NOT_ALLOWED"}],
            )
        ct = request.headers.get("content-type", "")
        media_type = ct.split(";", 1)[0].strip().lower()
        if (
            action in {"provide-evidence", "accept-claim"}
            and media_type == "multipart/form-data"
        ):
            raw = (
                "Content-Type: " + ct + "\r\nMIME-Version: 1.0\r\n\r\n"
            ).encode() + await request.body()
            message = BytesParser(policy=policy.default).parsebytes(raw)
            parts = [
                p
                for p in message.iter_parts()
                if p.get_param("name", header="content-disposition") == "input"
            ]
            if not message.is_multipart() or len(parts) != 1:
                raise ValueError("Exactly one input part required")
            body = json.loads(parts[0].get_payload(decode=True))
        else:
            if media_type != "application/json":
                raise ValueError("Use JSON or multipart input")
            body = await request.json()
        if not isinstance(body, dict):
            raise TypeError("Expected object")
        if action == "provide-evidence":
            if not isinstance(body.get("evidences"), list) or not body["evidences"]:
                raise ValueError("Evidence required")
            doc["evidences"].extend(deepcopy(body["evidences"]))
            doc["status"] = self.behavior.evidence_result_status
        elif action == "accept-claim":
            if body.get("accept_claim_type") != "REFUND":
                raise ValueError("Use REFUND")
            self.resolve(doc, self.behavior.buyer_win_code, True, "accept_claim")
        elif action == "escalate":
            doc.update(
                dispute_life_cycle_stage="CHARGEBACK",
                status=self.behavior.escalation_status,
            )
        elif action == "require-evidence":
            target = {
                "SELLER_EVIDENCE": "WAITING_FOR_SELLER_RESPONSE",
                "BUYER_EVIDENCE": "WAITING_FOR_BUYER_RESPONSE",
            }.get(body.get("action"))
            if not target:
                raise ValueError("Invalid target")
            doc["status"] = target
        elif action == "adjudicate":
            result = body.get("adjudication_outcome")
            if result not in {"BUYER_FAVOR", "SELLER_FAVOR"}:
                raise ValueError("Invalid outcome")
            buyer = result == "BUYER_FAVOR"
            self.resolve(
                doc,
                self.behavior.buyer_win_code
                if buyer
                else self.behavior.seller_win_code,
                buyer,
            )
        self.updated(doc)
        return self.doc(doc, str(request.base_url))


@router.post("/_mock/seed")
async def seed(body: dict, request: Request):
    async with request.app.state.control_lock:
        try:
            request.app.state.resource_service.seed(body)
        except (ValueError, KeyError, TypeError):
            raise PayoutError(422, "INVALID_REQUEST", "Invalid seed") from None
        return {"seeded": True}


@router.get("/v1/reporting/balances")
async def balances(
    request: Request, currency_code: str = "USD", as_of_time: str | None = None
):
    service = request.app.state.resource_service
    if not service.store.resources.snapshots:
        service.snapshot()
        service.schedule_snapshots()
    try:
        docs = [
            s
            for s in service.store.resources.snapshots
            if not as_of_time or instant(s["as_of_time"]) <= instant(as_of_time)
        ]
    except ValueError:
        raise PayoutError(422, "INVALID_REQUEST", "Invalid as_of_time") from None
    if not docs:
        raise PayoutError(404, "RESOURCE_NOT_FOUND", "No snapshot at requested time")
    doc = deepcopy(docs[-1])
    if currency_code != "USD":
        doc["balances"] = []
    return doc


@router.get("/v2/checkout/orders/{id}")
async def order(id: str, request: Request):
    return request.app.state.resource_service.get("orders", id)


@router.get("/v2/payments/captures/{id}")
async def capture(id: str, request: Request):
    return request.app.state.resource_service.get("captures", id)


@router.get("/v1/shipping/trackers")
async def trackers(request: Request, transaction_id: str):
    return {
        "trackers": [
            deepcopy(t)
            for t in request.app.state.store.resources.trackers
            if t["transaction_id"] == transaction_id
        ]
    }


@router.get("/v1/reporting/transactions")
async def transactions(
    request: Request,
    start_date: str | None = None,
    end_date: str | None = None,
    transaction_id: str | None = None,
    fields: str | None = None,
):
    store = request.app.state.store
    try:
        start = instant(start_date) if start_date else None
        end = instant(end_date) if end_date else store.clock.now
    except ValueError:
        raise PayoutError(422, "INVALID_REQUEST", "Invalid dates") from None
    cutoff = store.clock.now - timedelta(seconds=store.config.transactions_lag_seconds)
    items = [
        deepcopy(t)
        for t in store.resources.transactions
        if instant(t["transaction_info"]["transaction_initiation_date"])
        <= min(end, cutoff)
        and (
            not start
            or instant(t["transaction_info"]["transaction_initiation_date"]) >= start
        )
        and (
            not transaction_id
            or t["transaction_info"]["transaction_id"] == transaction_id
        )
    ]
    return {
        "transaction_details": items,
        "account_number": "MOCKACCT01",
        "start_date": start_date,
        "end_date": timestamp(end),
        "last_refreshed_datetime": timestamp(cutoff),
        "page": 1,
        "total_items": len(items),
        "total_pages": 1,
    }


@router.get("/v1/customer/disputes")
async def disputes(
    request: Request,
    start_time: str | None = None,
    disputed_transaction_id: str | None = None,
    dispute_state: str | None = None,
    page_size: int = Query(50, ge=1, le=200),
    next_page_token: str | None = None,
):
    service = request.app.state.resource_service
    try:
        start = instant(start_time) if start_time else None
        offset = (
            int(base64.urlsafe_b64decode(next_page_token).decode())
            if next_page_token
            else 0
        )
        if offset < 0:
            raise ValueError("Invalid token")
    except (ValueError, UnicodeError):
        raise PayoutError(422, "INVALID_REQUEST", "Invalid query") from None
    docs = [
        d
        for d in service.store.resources.disputes.values()
        if (not start or instant(d["create_time"]) >= start)
        and (not dispute_state or d["status"] == dispute_state)
        and (
            not disputed_transaction_id
            or any(
                disputed_transaction_id in t.values()
                for t in d["disputed_transactions"]
            )
        )
    ]
    docs.sort(key=lambda d: (d["create_time"], d["dispute_id"]))
    links = []
    if offset + page_size < len(docs):
        params = dict(request.query_params)
        params["next_page_token"] = base64.urlsafe_b64encode(
            str(offset + page_size).encode()
        ).decode()
        links = [
            {
                "href": str(request.url.replace(query=urlencode(params))),
                "rel": "next",
                "method": "GET",
            }
        ]
    return {
        "items": [
            service.doc(d, str(request.base_url))
            for d in docs[offset : offset + page_size]
        ],
        "links": links,
    }


@router.get("/v1/customer/disputes/{id}")
async def dispute(id: str, request: Request):
    service = request.app.state.resource_service
    return service.doc(service.get("disputes", id), str(request.base_url))


@router.post("/v1/customer/disputes/{id}/{action}")
async def dispute_action(id: str, action: str, request: Request):
    async with request.app.state.control_lock:
        try:
            return await request.app.state.resource_service.act(id, action, request)
        except (ValueError, KeyError, TypeError):
            raise PayoutError(422, "INVALID_REQUEST", "Invalid action body") from None


@router.post("/_mock/disputes")
async def new_dispute(body: dict, request: Request):
    async with request.app.state.control_lock:
        try:
            service = request.app.state.resource_service
            return service.doc(service.new_dispute(body), str(request.base_url))
        except (ValueError, KeyError, TypeError):
            raise PayoutError(
                422, "INVALID_REQUEST", "Invalid staged dispute"
            ) from None


@router.post("/_mock/disputes/{id}/{action}")
async def control_dispute(id: str, action: str, body: dict, request: Request):
    async with request.app.state.control_lock:
        service = request.app.state.resource_service
        doc = service.get("disputes", id)
        if doc["status"] == "RESOLVED":
            raise PayoutError(422, "ACTION_NOT_ALLOWED", "Dispute is resolved")
        if action == "buyer-message":
            if not isinstance(body.get("content"), str):
                raise PayoutError(422, "INVALID_REQUEST", "Content required")
            doc["messages"].append(
                {
                    "posted_by": "BUYER",
                    "time_posted": timestamp(service.store.clock.now),
                    "content": body["content"],
                }
            )
        elif action == "transition":
            if not isinstance(body.get("status"), str) or not isinstance(
                body.get("stage"), str
            ):
                raise PayoutError(422, "INVALID_REQUEST", "Status and stage required")
            doc.update(status=body["status"], dispute_life_cycle_stage=body["stage"])
        elif action == "buyer-close":
            service.resolve(doc, service.behavior.buyer_close_code)
        else:
            raise PayoutError(404, "RESOURCE_NOT_FOUND", "Control not found")
        service.updated(doc)
        return service.doc(doc, str(request.base_url))
