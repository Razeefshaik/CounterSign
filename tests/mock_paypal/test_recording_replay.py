"""Replay the C09 HTTP corpus only through FastAPI's in-process transport.

Resources are seeded from recorded observations before each isolated request.
Generated IDs, callback URLs, OAuth credentials and signatures are localised.
Shape comparison requires every recorded field/type, allowing optional extras.
Separate state-transition tests assert financial effects, statuses and permissions.
Buyer setup writes are outside CONTRACTS 5; absent responses and non-JSON edge
errors have no PayPal response shape. Every such exclusion is accounted for.
"""

import json
from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from mock_paypal.app import create_app
from mock_paypal.clock import MockClock

ROOT = Path(__file__).resolve().parents[2]
RECORDINGS = ROOT / "recordings/sandbox"
CASES = []
for file in sorted(RECORDINGS.glob("*/*/[0-9]*_*.json")):
    document = json.loads(file.read_text(encoding="utf-8"))
    if all(key in document for key in ["method", "path", "response_status", "response_body"]):
        CASES.append((file.relative_to(RECORDINGS).as_posix(), document))


def excluded(doc):
    if doc["response_status"] is None:
        return "transport failure without an HTTP response"
    if not isinstance(doc["response_body"], dict):
        return "non-JSON edge response outside the PayPal API envelope"
    if doc["method"] == "POST" and doc["path"].startswith("/v2/checkout/orders"):
        return "buyer setup write outside CONTRACTS section 5"
    return None


REPLAY = [(name, doc) for name, doc in CASES if excluded(doc) is None]
ITEMS = {}
DISPUTES = {}
for _, doc in CASES:
    body = doc["response_body"]
    if doc["method"] != "GET" or doc["response_status"] != 200 or not isinstance(body, dict):
        continue
    if "payout_item_id" in body:
        ITEMS[body["payout_item_id"]] = body
    for item in body.get("items", []):
        if "payout_item_id" in item:
            ITEMS[item["payout_item_id"]] = item
    if "dispute_id" in body:
        DISPUTES.setdefault(body["dispute_id"], body)


def assert_shape(actual, recorded, location="body"):
    if isinstance(recorded, dict):
        assert isinstance(actual, dict), location
        assert recorded.keys() <= actual.keys(), (
            f"{location}: missing {recorded.keys() - actual.keys()}"
        )
        for key, value in recorded.items():
            assert_shape(actual[key], value, location + "." + key)
    elif isinstance(recorded, list):
        assert isinstance(actual, list), location
        assert len(actual) >= len(recorded), location + ": missing array entries"
        if not recorded:
            assert not actual, location + ": expected empty array"
        for index, value in enumerate(recorded):
            assert_shape(actual[index], value, f"{location}[{index}]")
    else:
        assert type(actual) is type(recorded), (
            f"{location}: expected {type(recorded).__name__}, got {type(actual).__name__}"
        )


def payout_seed(item, header=None):
    header = header or {}
    payload = item["payout_item"]
    return {
        "payout_item_id": item["payout_item_id"],
        "payout_batch_id": item["payout_batch_id"],
        "receiver": payload["receiver"],
        "amount": payload["amount"]["value"],
        "status": item["transaction_status"],
        "sender_batch_id": item.get(
            "sender_batch_id",
            header.get("sender_batch_header", {}).get("sender_batch_id", "recorded-history"),
        ),
        "payout_item": payload,
        "payout_item_fee": item.get("payout_item_fee", {"value": "0.00"}),
        "errors": item.get("errors"),
        "time_processed": item.get("time_processed"),
        "created_at": header.get("time_created", "2026-10-23T00:00:00Z"),
        "batch_status": header.get(
            "batch_status", "PENDING" if item["transaction_status"] == "PENDING" else "SUCCESS"
        ),
        "sender_batch_header": header.get(
            "sender_batch_header",
            {"sender_batch_id": item.get("sender_batch_id", "recorded-history")},
        ),
    }


def seed_for(doc):
    body, method = doc["response_body"], doc["method"]
    path = urlsplit(doc["path"]).path
    seed = {"balances": {"available": "1000000.00", "withheld": "0.00", "currency_code": "USD"}}
    if method == "GET" and path == "/v1/reporting/balances":
        balance = body["balances"][0]
        seed["balances"] = {
            "available": balance["available_balance"]["value"],
            "withheld": balance["withheld_balance"]["value"],
            "currency_code": "USD",
        }
    elif method == "GET" and path == "/v1/reporting/transactions":
        seed["transactions"] = body.get("transaction_details", [])
    elif method == "GET" and path.startswith("/v2/payments/captures/"):
        seed["captures"] = [body]
    elif method == "GET" and path.startswith("/v2/checkout/orders/"):
        seed["orders"] = [body]
    elif method == "GET" and path == "/v1/shipping/trackers":
        seed["trackers"] = body["trackers"]
    elif (
        method == "GET"
        and path.startswith("/v1/customer/disputes")
        and doc["response_status"] == 200
    ):
        seed["disputes"] = body.get("items", [body])
    elif method == "POST" and path.startswith("/v1/customer/disputes/"):
        id = path.split("/")[4]
        seed["disputes"] = [deepcopy(DISPUTES[id])]
        action = path.rsplit("/", 1)[1]
        if action == "provide-evidence":
            # D10's last GET before this submission, not a fabricated permission.
            before = next(
                d["response_body"]
                for name, d in CASES
                if name.endswith("D10/13_spare_25_poll.json")
            )
            seed["disputes"] = [before]
        if action == "adjudicate":
            seed["disputes"][0]["status"] = "UNDER_REVIEW"
    elif method == "GET" and "payout_item_id" in body and "ERRPYO" not in path:
        seed["payout_items"] = [payout_seed(body)]
    elif method == "GET" and "batch_header" in body and "ERRPYO" not in path:
        seed["payout_items"] = [payout_seed(item, body["batch_header"]) for item in body["items"]]
    elif method == "POST" and path.endswith("/cancel") and "ERRPYO" not in path:
        id = path.split("/")[4]
        item = deepcopy(body if doc["response_status"] == 200 else ITEMS[id])
        item["transaction_status"] = (
            "UNCLAIMED"
            if doc["response_status"] == 200
            else "RETURNED"
            if body["name"] == "ITEM_ALREADY_CANCELLED"
            else "SUCCESS"
        )
        entry = payout_seed(item)
        entry["cancelled"] = body.get("name") == "ITEM_ALREADY_CANCELLED"
        seed["payout_items"] = [entry]
    return seed


@pytest.mark.parametrize("name,exchange", REPLAY, ids=[name for name, _ in REPLAY])
def test_recorded_exchange_shape(name, exchange):
    doc = deepcopy(exchange)
    app = create_app(control_enabled=True, webhook_delivery=lambda *args: None)
    # Every request uses an isolated observed state; no real credentials or sockets.
    with TestClient(app) as client:
        current = datetime.fromisoformat(doc["t_sent"])
        if doc["response_status"] == 400 and "start_time=" in doc["path"]:
            requested = parse_qs(urlsplit(doc["path"]).query)["start_time"][0]
            current = datetime.fromisoformat(requested) - timedelta(seconds=1)
        app.state.store.clock = MockClock(current)
        seeded = client.post("/_mock/seed", json=seed_for(doc))
        assert seeded.status_code == 200, name
        auth = client.post(
            "/v1/oauth2/token",
            auth=("mock-replay", "mock-only"),
            data={"grant_type": "client_credentials"},
        )
        client.headers["Authorization"] = "Bearer " + auth.json()["access_token"]
        path, method, body = doc["path"], doc["method"], doc["request_body"]
        kwargs = {}
        if path == "/v1/oauth2/token":
            kwargs = {
                "auth": ("mock-replay", "mock-only"),
                "data": {"grant_type": "client_credentials"},
            }
        elif isinstance(body, (dict, list)):
            kwargs = {"json": body}
        elif body:
            kwargs = {
                "content": body,
                "headers": {"content-type": doc["request_headers"]["content-type"]},
            }
        if (
            method == "POST"
            and path == "/v1/payments/payouts"
            and doc["response_status"] == 400
            and "ERRPYO" not in json.dumps(body)
        ):
            assert client.post(path, json=body).status_code == 201
        service = app.state.webhook_service
        if path.startswith("/v1/notifications/"):
            reg_body = {
                "url": "http://localhost:8098/mock-callback",
                "event_types": [{"name": "*"}],
            }
            if path == "/v1/notifications/webhooks":
                kwargs["json"]["url"] = reg_body["url"]
            else:
                reg = client.post("/v1/notifications/webhooks", json=reg_body).json()
                if method == "PATCH":
                    path = "/v1/notifications/webhooks/" + reg["id"]
                    for entry in kwargs["json"]:
                        entry["value"] = reg_body["url"]
                    app.state.store.webhooks.registrations[reg["id"]]["event_types"] = doc[
                        "response_body"
                    ]["event_types"]
                elif path.endswith("verify-webhook-signature"):
                    source = body["webhook_event"]
                    event = service.emit(source["event_type"], source["resource"])
                    record = app.state.store.webhooks.events[event["id"]]
                    headers = service.headers(record, reg["id"])
                    kwargs["json"] = {
                        "webhook_event": event,
                        "webhook_id": reg["id"],
                        "auth_algo": headers["paypal-auth-algo"],
                        "cert_url": headers["paypal-cert-url"],
                        "transmission_id": headers["paypal-transmission-id"],
                        "transmission_time": headers["paypal-transmission-time"],
                        "transmission_sig": headers["paypal-transmission-sig"],
                    }
                elif path.endswith("/resend"):
                    source = doc["response_body"]
                    event = service.emit(source["event_type"], source["resource"])
                    path = "/v1/notifications/webhooks-events/" + event["id"] + "/resend"
                elif method == "GET" and path.startswith("/v1/notifications/webhooks-events"):
                    for event in doc["response_body"]["events"]:
                        service.emit(event["event_type"], event["resource"])
                elif path.endswith("simulate-event"):
                    # Simulator payloads are generated fixture data, not payment state.
                    kwargs["json"]["resource"] = doc["response_body"]["resource"]
        response = client.request(method, path, **kwargs)
        assert response.status_code == doc["response_status"], name
        assert response.headers.get("paypal-debug-id"), name
        actual = response.json()
        assert_shape(actual, doc["response_body"])
        if "name" in doc["response_body"]:
            assert actual["name"] == doc["response_body"]["name"]
        if "transaction_status" in doc["response_body"]:
            assert actual["transaction_status"] == doc["response_body"]["transaction_status"]
        if path.endswith("verify-webhook-signature"):
            assert actual["verification_status"] == doc["response_body"]["verification_status"]


def test_every_published_exchange_is_accounted_for():
    assert (RECORDINGS / "INDEX.md").exists()
    assert len(CASES) == 1041
    reasons = Counter(excluded(doc) for _, doc in CASES)
    assert reasons == {
        None: 999,
        "transport failure without an HTTP response": 34,
        "non-JSON edge response outside the PayPal API envelope": 1,
        "buyer setup write outside CONTRACTS section 5": 7,
    }
    assert {name.split("/")[1] for name, _ in REPLAY} == {f"D{i}" for i in range(1, 14)}
