"""Request faults at the ASGI boundary; only mock-clock waits."""

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from mock_paypal.clock import timestamp
from mock_paypal.payout_models import amount
from mock_paypal.payouts import PayoutError

router = APIRouter()
# Contract D7 fixtures, replaced through create_app(negative_values=...) after recordings.
NEGATIVE_VALUES = {
    "ERRPYO005": (422, "INSUFFICIENT_FUNDS"),
    "ERRPYO006": (500, "INTERNAL_ERROR"),
    "ERRPYO014": (422, "USER_BUSINESS_ERROR"),
    "ERRPYO036": (504, "REQUEST_TIMEOUT_EXCEEDED"),
    "ERRPYO039": (409, "PREVIOUS_REQUEST_IN_PROGRESS"),
    "ERRPYO007": (400, "ITEM_ALREADY_CANCELLED"),
    "ERRPYO008": (400, "ITEM_CANCELLATION_FAILED"),
    "ERRPYO009": (400, "ITEM_INCORRECT_STATUS"),
    "ERRPYO015": (200, "CLOSED_MARKET"),
    "ERRPYO047": (200, "RECEIVER_UNREGISTERED"),
    "ERRPYO052": (200, "RISK_DECLINE"),
    "ERRPYOB002": (200, "PENDING"),
    "ERRPYOB003": (200, "PROCESSING"),
    "ERRPYOB004": (200, "DENIED"),
}


class Strict(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class Match(Strict):
    method: str
    path: str
    nth: int = Field(default=1, ge=1)
    count: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def valid_regex(self):
        try:
            re.compile(self.path)
        except re.error:
            raise ValueError("Invalid path pattern") from None
        return self


class Action(Strict):
    type: Literal[
        "drop_response",
        "timeout",
        "http_error",
        "connection_reset",
        "delay",
        "test_value",
    ]
    after_commit: bool = False
    seconds: int = Field(default=0, ge=0)
    status: Literal[500, 503] = 500
    body: dict | None = None
    value: str | None = None

    @model_validator(mode="after")
    def supported_value(self):
        if self.type == "test_value" and self.value not in NEGATIVE_VALUES:
            raise ValueError("Unsupported D7 test value")
        return self


class FaultBody(Strict):
    id: str | None = None
    match: Match
    action: Action


@dataclass
class Fault:
    doc: dict
    seen: int = 0
    used: int = 0


@dataclass
class FaultState:
    faults: list[Fault] = field(default_factory=list)
    requests: list[dict] = field(default_factory=list)


@router.post("/_mock/faults", status_code=201)
async def add_fault(body: FaultBody, request: Request):
    store = request.app.state.store
    doc = body.model_dump()
    doc["id"] = doc["id"] or store.new_id("FAULT")
    if any(f.doc["id"] == doc["id"] for f in store.faults.faults):
        raise PayoutError(409, "DUPLICATE_ID", "Fault ID already exists")
    store.faults.faults.append(Fault(doc))
    return {"id": doc["id"]}


@router.get("/_mock/faults")
async def list_faults(request: Request):
    return {"items": [f.doc for f in request.app.state.store.faults.faults]}


@router.delete("/_mock/faults/{id}")
async def delete_fault(id: str, request: Request):
    state = request.app.state.store.faults
    found = next((f for f in state.faults if f.doc["id"] == id), None)
    if found is None:
        raise PayoutError(404, "RESOURCE_NOT_FOUND", "Fault not found")
    state.faults.remove(found)
    return {"id": id}


@router.get("/_mock/ground-truth")
async def ground_truth(request: Request):
    store = request.app.state.store
    state = store.payouts
    items = []
    for item in state.items.values():
        batch = state.batches[item.batch_id]
        items.append(
            {
                "payout_item_id": item.id,
                "payout_batch_id": batch.id,
                "sender_batch_id": batch.sender_header["sender_batch_id"],
                "request_id": batch.request_id,
                "receiver": item.payload["receiver"],
                "amount": item.payload["amount"],
                "status": item.status,
                "money_left_account": item.status
                in {"SUCCESS", "PENDING", "ONHOLD", "BLOCKED", "UNCLAIMED"},
                "created_at": timestamp(item.created_at),
                "synthesised": item.synthesised,
            }
        )

    def balance(value):
        return {"value": amount(value)["value"], "currency_code": "USD"}

    return {
        "payout_items": items,
        "refunds": store.refunds,
        "requests": store.faults.requests,
        "balances": {
            "available": balance(state.available_minor),
            "withheld": balance(state.withheld_minor),
        },
    }


class TransitionBody(Strict):
    to: Literal[
        "ONHOLD", "BLOCKED", "FAILED", "SUCCESS", "PENDING", "REFUNDED", "REVERSED"
    ]
    emit_webhook: bool = True


class ClaimBody(Strict):
    when: Literal["now", "during_next_cancel"] = "now"


@router.post("/_mock/payout-items/{id}/transition")
async def transition(id: str, body: TransitionBody, request: Request):
    async with request.app.state.control_lock:
        service = request.app.state.payout_service
        item = service.get_item(id)
        service.transition(
            item, body.to, synthesised=True, emit_webhook=body.emit_webhook
        )
        return service.item_doc(item, str(request.base_url), standalone=True)


@router.post("/_mock/payout-items/{id}/claim")
async def claim(id: str, body: ClaimBody, request: Request):
    async with request.app.state.control_lock:
        service = request.app.state.payout_service
        item = service.get_item(id)
        if item.status != "UNCLAIMED":
            raise PayoutError(
                400, "ITEM_INCORRECT_STATUS", "Only unclaimed items can be claimed"
            )
        if body.when == "during_next_cancel":
            item.claim_during_cancel = True
        else:
            service.transition(item, "SUCCESS")
        return service.item_doc(item, str(request.base_url), standalone=True)


class FaultMiddleware:
    def __init__(self, app, store, negative_values=None):
        self.app, self.store = app, store
        self.negative_values = negative_values or NEGATIVE_VALUES

    async def wait(self, seconds):
        future = asyncio.get_running_loop().create_future()
        self.store.clock.schedule(
            seconds, lambda: None if future.done() else future.set_result(None)
        )
        await future

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"].startswith("/_mock/"):
            return await self.app(scope, receive, send)
        scope.setdefault("state", {}).setdefault("debug_id", uuid4().hex[:13])
        headers = dict(scope["headers"])
        token = headers.get(b"authorization", b"").decode().partition(" ")[2]
        authenticated = self.store.valid_token(token)
        fault = None
        if authenticated:
            for candidate in self.store.faults.faults:
                match = candidate.doc["match"]
                if match["method"].upper() == scope["method"] and re.search(
                    match["path"], scope["path"]
                ):
                    candidate.seen += 1
                    if (
                        candidate.seen >= match["nth"]
                        and candidate.used < match["count"]
                    ):
                        candidate.used += 1
                        fault = candidate
                        break
        action = fault.doc["action"] if fault else None
        messages = []

        async def capture(message):
            messages.append(message)

        process = not action or action["type"] == "delay" or action.get("after_commit")
        if action and action["type"] == "delay":
            await self.wait(action["seconds"])
        if process:
            await self.app(scope, receive, capture)
        status = next(
            (m["status"] for m in messages if m["type"] == "http.response.start"), None
        )
        entry = {
            "at": timestamp(self.store.clock.now),
            "method": scope["method"],
            "path": scope["path"],
            "status": status,
            "request_id": headers.get(b"paypal-request-id", b"").decode() or None,
            "fault_id": fault.doc["id"] if fault else None,
        }
        self.store.faults.requests.append(entry)
        if not action or action["type"] == "delay":
            for message in messages:
                await send(message)
            return
        kind = action["type"]
        if kind == "timeout":
            await self.wait(action["seconds"])
        if kind in {"drop_response", "connection_reset", "timeout"}:
            # Advertise a body and terminate before delivering it. Actual HTTP clients
            # observe an incomplete response/connection reset, never a synthetic 500.
            await send(
                {
                    "type": "http.response.start",
                    "status": status or 200,
                    "headers": [
                        (b"content-length", b"1"),
                        (b"paypal-debug-id", uuid4().hex[:13].encode()),
                    ],
                }
            )
            raise ConnectionResetError("Injected mock connection loss")
        debug = uuid4().hex[:13]
        if kind == "test_value":
            code, name = self.negative_values[action["value"]]
            body = self.negative_doc(action["value"], name, debug, messages)
        else:
            code = action["status"]
            body = action.get("body") or self.envelope("INTERNAL_ERROR", debug)
        encoded = json.dumps(body).encode()
        if status is None:
            entry["status"] = code
        await send(
            {
                "type": "http.response.start",
                "status": code,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(encoded)).encode()),
                    (b"paypal-debug-id", debug.encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": encoded})

    @staticmethod
    def envelope(name, debug):
        return {
            "name": name,
            "message": "Injected mock response",
            "debug_id": debug,
            "information_link": "https://developer.paypal.com/api/rest/responses/",
            "details": [],
            "links": [],
        }

    def negative_doc(self, value, name, debug, messages):
        if value.startswith("ERRPYOB"):
            return {
                "batch_header": {"payout_batch_id": value, "batch_status": name},
                "items": [],
                "links": [],
            }
        if value in {"ERRPYO015", "ERRPYO047", "ERRPYO052"}:
            raw = b"".join(m.get("body", b"") for m in messages)
            body = (
                json.loads(raw)
                if raw
                else {
                    "payout_item_id": value,
                    "payout_batch_id": "MOCKB-NEGATIVE",
                    "transaction_id": "MOCKT-NEGATIVE",
                    "activity_id": "MOCKACT-NEGATIVE",
                    "payout_item_fee": amount(0),
                    "payout_item": {
                        "recipient_type": "EMAIL",
                        "receiver": "negative@example.com",
                        "amount": amount(100),
                        "sender_item_id": "negative",
                    },
                    "time_processed": timestamp(self.store.clock.now),
                    "links": [],
                }
            )
            body.update(
                transaction_status="FAILED",
                errors={"name": name, "message": "Injected D7 result"},
            )
            return body
        return self.envelope(name, debug)
