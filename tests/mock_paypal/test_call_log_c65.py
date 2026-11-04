"""C65: real mock middleware, redaction, bounded storage and Friday persistence."""

import json

import httpx
import pytest

from mock_paypal.app import create_app
from mock_paypal.call_log import append, document, short_hash
from mock_paypal.checkpoint import restore, snapshot
from mock_paypal.faults import Fault


async def token(client):
    result = await client.post("/v1/oauth2/token", auth=("fake-client", "fake-secret"), data={"grant_type":"client_credentials"})
    value = result.json()["access_token"]
    client.headers["authorization"] = "Bearer " + value
    return value


def payout():
    return {"sender_batch_header":{"sender_batch_id":"private-c65-sender"},
            "items":[{"recipient_type":"EMAIL", "receiver":"private@example.test",
                      "amount":{"value":"1.00", "currency":"USD"}, "sender_item_id":"c65-item"}]}


@pytest.mark.asyncio
async def test_templates_and_hashes_never_expose_private_inputs():
    app = create_app(control_enabled=True)
    app.state.store.payouts.available_minor = 10000
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost:8099") as client:
        access = await token(client)
        result = await client.post("/v1/payments/payouts", json=payout(), headers={"PayPal-Request-Id":"private-c65-key"})
        assert result.status_code == 201
        batch = result.json()["batch_header"]["payout_batch_id"]
        assert (await client.get("/v1/payments/payouts/" + batch)).status_code == 200
        rows = document(app.state.store)["rows"]
        assert rows[0]["route"] == "/v1/payments/payouts/{id}"
        assert rows[1]["request_id_hash"] == short_hash("private-c65-key")
        assert rows[1]["sender_batch_id_hash"] == short_hash("private-c65-sender")
        public = json.dumps(document(app.state.store))
        for private in (access, batch, "private@example.test", "private-c65-key", "private-c65-sender", "fake-secret", "fake-client", "Authorization"):
            assert private not in public
        assert rows[0]["source"] == "Simulated: PayPal mock"


@pytest.mark.asyncio
async def test_duplicate_400_has_same_hash_and_original_batch_marker():
    app = create_app(control_enabled=True)
    app.state.store.payouts.available_minor = 10000
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost:8099") as client:
        await token(client)
        for expected in (201, 400):
            result = await client.post("/v1/payments/payouts", json=payout(), headers={"PayPal-Request-Id":"c65-same"})
            assert result.status_code == expected
        second, first = document(app.state.store)["rows"][:2]
        assert second["original_batch"] and second["error"] == "USER_BUSINESS_ERROR"
        assert first["sender_batch_id_hash"] == second["sender_batch_id_hash"]


@pytest.mark.asyncio
async def test_after_commit_lost_response_and_retry_are_both_observed():
    app = create_app(control_enabled=True)
    app.state.store.payouts.available_minor = 10000
    app.state.store.faults.faults.append(Fault({"id":"c65-lost", "match":{
        "method":"POST", "path":"^/v1/payments/payouts$", "nth":1, "count":1},
        "action":{"type":"drop_response", "after_commit":True}}))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://localhost:8099") as client:
        await token(client)
        await client.post("/v1/payments/payouts", json=payout(), headers={"PayPal-Request-Id":"c65-lost-key"})
        assert (await client.post("/v1/payments/payouts", json=payout(), headers={"PayPal-Request-Id":"c65-lost-key"})).status_code == 400
        second, first = document(app.state.store)["rows"][:2]
        assert first["incomplete"] and first["status"] == 201
        assert second["original_batch"] and second["status"] == 400
        assert second["request_id_hash"] == first["request_id_hash"]


@pytest.mark.asyncio
async def test_controls_excluded_reset_clears_and_remote_control_is_refused():
    app = create_app(control_enabled=True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost:8099") as client:
        await token(client)
        await client.get("/_mock/clock")
        assert document(app.state.store)["total"] == 1
        assert (await client.get("/_mock/paypal-calls")).status_code == 200
        assert document(app.state.store)["total"] == 1
        assert (await client.post("/_mock/reset", json={})).status_code == 200
        assert document(app.state.store) == {"source":"Simulated: PayPal mock", "limit":500, "total":0, "counts":{}, "rows":[]}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("203.0.113.1", 1000)), base_url="http://localhost") as client:
        assert (await client.get("/_mock/paypal-calls")).status_code == 404
    disabled = create_app(control_enabled=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=disabled), base_url="http://localhost") as client:
        assert (await client.get("/_mock/paypal-calls")).status_code == 404


def test_bounded_log_totals_and_friday_round_trip():
    app = create_app(control_enabled=True)
    for _ in range(503):
        append(app.state.store, method="GET", route="/v1/payments/payouts/PRIVATE-ID", status=200)
    before = document(app.state.store)
    saved = json.loads(json.dumps(snapshot(app.state.store)))
    app.state.store.reset()
    restore(app, saved)
    assert document(app.state.store) == before
    assert app.state.store.calls.maxlen == 500 and before["total"] == 503
    assert before["counts"] == {"Payouts":503} and len(before["rows"]) == 500


@pytest.mark.asyncio
async def test_webhook_delivery_has_fixed_route_and_survives_pending_timer_restore():
    async def delivery(*_args):
        return 200
    app = create_app(control_enabled=True, webhook_delivery=delivery)
    app.state.store.webhooks.registrations["WH-MOCK-C65"] = {
        "url":"http://localhost:8199/webhooks/paypal", "event_types":[{"name":"*"}]}
    app.state.store.emit("CUSTOMER.DISPUTE.CREATED", {"dispute_id":"PRIVATE-ID"})
    restore(app, json.loads(json.dumps(snapshot(app.state.store))))
    await app.state.store.clock.advance(5)
    row = document(app.state.store)["rows"][0]
    assert (row["product"], row["direction"], row["route"], row["status"]) == ("Webhooks", "PayPal to app", "/webhooks/paypal", 200)
    assert "PRIVATE-ID" not in json.dumps(row)
