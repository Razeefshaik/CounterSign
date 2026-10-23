"""Test-only RSA signing, local webhook delivery, and deterministic faults."""

import base64
import binascii
import fnmatch
import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import Response

from mock_paypal.clock import timestamp
from mock_paypal.payouts import PayoutError
from mock_paypal.test_signing_fixture import CERTIFICATE, D, E, N

router = APIRouter()
DIGEST_INFO = bytes.fromhex("3031300d060960864801650304020105000420")


def encoded_message(message):
    tail = DIGEST_INFO + hashlib.sha256(message).digest()
    length = (N.bit_length() + 7) // 8
    return b"\x00\x01" + b"\xff" * (length - len(tail) - 3) + b"\x00" + tail


def sign(message):
    length = (N.bit_length() + 7) // 8
    return base64.b64encode(
        pow(int.from_bytes(encoded_message(message), "big"), D, N).to_bytes(
            length, "big"
        )
    ).decode()


def verify(message, signature):
    try:
        raw = base64.b64decode(signature, validate=True)
        length = (N.bit_length() + 7) // 8
        return (
            len(raw) == length
            and int.from_bytes(raw, "big") < N
            and pow(int.from_bytes(raw, "big"), E, N).to_bytes(length, "big")
            == encoded_message(message)
        )
    except (ValueError, TypeError):
        return False


def signed_message(id, time, webhook_id, raw):
    return f"{id}|{time}|{webhook_id}|{binascii.crc32(raw) & 0xFFFFFFFF}".encode()


def local_url(value):
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
        or parsed.username
        or parsed.password
    ):
        raise PayoutError(422, "INVALID_REQUEST", "Mock callbacks must use a local URL")
    return value


@dataclass
class WebhookState:
    registrations: dict = field(default_factory=dict)
    events: dict = field(default_factory=dict)
    deliveries: list = field(default_factory=list)
    faults: list = field(default_factory=list)
    held: list = field(default_factory=list)


class WebhookService:
    def __init__(self, store, delivery=None, base_url="http://localhost:8099"):
        self.store, self.delivery, self.base_url = store, delivery, base_url

    def emit(self, type, resource):
        state = self.store.webhooks
        event = {
            "id": self.store.new_id("WH-MOCK-EVT-"),
            "event_version": "1.0",
            "create_time": timestamp(self.store.clock.now),
            "resource_type": "payouts_batch"
            if type.startswith("PAYMENT.PAYOUTSBATCH")
            else "payouts_item"
            if "PAYOUTS-ITEM" in type
            else "dispute"
            if "DISPUTE" in type
            else "capture",
            "event_type": type,
            "summary": "Simulated: mock PayPal",
            "resource": deepcopy(resource),
            "links": [],
        }
        raw = json.dumps(event, separators=(",", ":"), ensure_ascii=False).encode()
        record = {"event": event, "raw": raw, "headers": {}}
        state.events[event["id"]] = record
        action = None
        for fault in state.faults:
            match = fault["match"]
            if fnmatch.fnmatchcase(type, match["event_type"]):
                fault["seen"] += 1
                if fault["seen"] >= match.get("nth", 1) and fault["used"] < match.get(
                    "count", 1
                ):
                    fault["used"] += 1
                    action = fault["action"]
                    break
        for id, registration in state.registrations.items():
            if any(
                fnmatch.fnmatchcase(type, item["name"])
                for item in registration["event_types"]
            ):
                if action and action["type"] == "reorder_after":
                    state.held.append((record, id, action["event_type"]))
                else:
                    self.schedule(record, id, action)
        # Target is scheduled first; held events follow it at the same mock time.
        for held in list(state.held):
            record_before, id, target = held
            if fnmatch.fnmatchcase(type, target):
                state.held.remove(held)
                self.schedule(record_before, id)
        return event

    def headers(self, record, id):
        transmission_id = self.store.new_id("TRANS-")
        time = timestamp(self.store.clock.now)
        signature = sign(signed_message(transmission_id, time, id, record["raw"]))
        headers = {
            "paypal-transmission-id": transmission_id,
            "paypal-transmission-time": time,
            "paypal-transmission-sig": signature,
            "paypal-cert-url": self.base_url + "/_mock/cert.pem",
            "paypal-auth-algo": "SHA256withRSA",
            "content-type": "application/json",
        }
        record["headers"].setdefault(id, []).append(headers)
        return headers

    def schedule(self, record, id, action=None, headers=None):
        state = self.store.webhooks
        action = action or {}
        if action.get("type") == "drop":
            return
        delay = self.store.config.webhook_delay_seconds
        if action.get("type") == "delay":
            delay += action["seconds"]
        copies = action.get("copies", 1) if action.get("type") == "duplicate" else 1
        for _ in range(copies):
            delivery_headers = headers or self.headers(record, id)

            async def deliver(delivery_headers=delivery_headers):
                if state is not self.store.webhooks or id not in state.registrations:
                    return
                url = state.registrations[id]["url"]
                status = None
                try:
                    if self.delivery:
                        status = await self.delivery(
                            url, delivery_headers, record["raw"]
                        )
                    else:
                        async with httpx.AsyncClient(
                            trust_env=False, follow_redirects=False, timeout=5
                        ) as client:
                            status = (
                                await client.post(
                                    url, headers=delivery_headers, content=record["raw"]
                                )
                            ).status_code
                except httpx.HTTPError:
                    status = 0
                state.deliveries.append(
                    {
                        "event_id": record["event"]["id"],
                        "webhook_id": id,
                        "status": status,
                        "at": timestamp(self.store.clock.now),
                    }
                )

            self.store.clock.schedule(delay, deliver)

    def valid(self, body):
        record = self.store.webhooks.events.get(body.get("webhook_event", {}).get("id"))
        if not record or body.get("webhook_event") != record["event"]:
            return False
        id = body.get("webhook_id")
        if id not in self.store.webhooks.registrations:
            return False
        if (
            body.get("auth_algo") != "SHA256withRSA"
            or body.get("cert_url") != self.base_url + "/_mock/cert.pem"
        ):
            return False
        known = any(
            h["paypal-transmission-id"] == body.get("transmission_id")
            and h["paypal-transmission-time"] == body.get("transmission_time")
            for h in record["headers"].get(id, [])
        )
        return known and verify(
            signed_message(
                body.get("transmission_id"),
                body.get("transmission_time"),
                id,
                record["raw"],
            ),
            body.get("transmission_sig"),
        )


@router.get("/_mock/cert.pem")
async def cert():
    return Response(CERTIFICATE, media_type="application/x-pem-file")


@router.post("/v1/notifications/webhooks", status_code=201)
async def register(body: dict, request: Request):
    store = request.app.state.store
    if (
        not isinstance(body.get("url"), str)
        or not isinstance(body.get("event_types"), list)
        or not body["event_types"]
    ):
        raise PayoutError(422, "INVALID_REQUEST", "URL and event types required")
    if not all(
        isinstance(t, dict) and isinstance(t.get("name"), str)
        for t in body["event_types"]
    ):
        raise PayoutError(422, "INVALID_REQUEST", "Invalid event types")
    doc = {
        "id": store.new_id("WH-MOCK-"),
        "url": local_url(body["url"]),
        "event_types": body["event_types"],
    }
    store.webhooks.registrations[doc["id"]] = deepcopy(doc)
    return doc


@router.get("/v1/notifications/webhooks")
async def registrations(request: Request):
    return {"webhooks": list(request.app.state.store.webhooks.registrations.values())}


@router.get("/v1/notifications/webhooks-events")
async def events(request: Request):
    return {
        "events": [
            r["event"] for r in request.app.state.store.webhooks.events.values()
        ],
        "links": [],
    }


@router.post("/v1/notifications/verify-webhook-signature")
async def verification(body: dict, request: Request):
    try:
        result = request.app.state.webhook_service.valid(body)
    except (ValueError, AttributeError, TypeError):
        result = False
    return {"verification_status": "SUCCESS" if result else "FAILURE"}


@router.post("/v1/notifications/webhooks-events/{id}/resend", status_code=202)
async def resend(id: str, request: Request):
    service = request.app.state.webhook_service
    record = service.store.webhooks.events.get(id)
    if not record:
        raise PayoutError(404, "RESOURCE_NOT_FOUND", "Event not found")
    for webhook_id in record["headers"]:
        service.schedule(record, webhook_id)
    return {"id": id}


@router.post("/_mock/webhook-faults", status_code=201)
async def webhook_fault(body: dict, request: Request):
    match, action = body.get("match", {}), body.get("action", {})
    if not isinstance(match.get("event_type"), str) or action.get("type") not in {
        "drop",
        "duplicate",
        "delay",
        "reorder_after",
    }:
        raise PayoutError(422, "INVALID_REQUEST", "Invalid webhook fault")
    if action["type"] == "reorder_after" and not isinstance(
        action.get("event_type"), str
    ):
        raise PayoutError(422, "INVALID_REQUEST", "Target event type required")
    for value in [match.get("nth", 1), match.get("count", 1), action.get("copies", 1)]:
        if type(value) is not int or value < 1:
            raise PayoutError(422, "INVALID_REQUEST", "Invalid fault count")
    if type(action.get("seconds", 0)) is not int or action.get("seconds", 0) < 0:
        raise PayoutError(422, "INVALID_REQUEST", "Invalid delay")
    store = request.app.state.store
    id = store.new_id("WH-FAULT-")
    store.webhooks.faults.append(
        {"id": id, "match": match, "action": action, "seen": 0, "used": 0}
    )
    return {"id": id}


@router.post("/_mock/webhooks/forge", status_code=202)
async def forge(body: dict, request: Request):
    service = request.app.state.webhook_service
    state = service.store.webhooks
    old = state.events.get(body.get("replay_of"))
    if body.get("replay_of") and not old:
        raise PayoutError(404, "RESOURCE_NOT_FOUND", "Replay event not found")
    for id in state.registrations:
        if old:
            record = old
        else:
            event = {
                "id": service.store.new_id("FORGED-"),
                "event_type": body.get("event_type", ""),
                "resource": body.get("resource", {}),
                "create_time": timestamp(service.store.clock.now),
            }
            record = {"event": event, "raw": json.dumps(event).encode(), "headers": {}}
        headers = service.headers(record, id)
        mode = body.get("signature")
        if mode == "missing":
            headers.pop("paypal-transmission-sig", None)
        elif mode == "wrong_webhook_id":
            headers["paypal-transmission-sig"] = sign(
                signed_message(
                    headers["paypal-transmission-id"],
                    headers["paypal-transmission-time"],
                    "WRONG-ID",
                    record["raw"],
                )
            )
        elif mode == "invalid" or not old:
            headers["paypal-transmission-sig"] = "invalid"
        service.schedule(record, id, headers=headers)
    return {"sent": True}
