"""C62 snapshots: local files, precise state and timers, no external calls."""

import json
from datetime import timedelta

import httpx
import pytest

from mock_paypal.app import create_app
from mock_paypal.checkpoint import CheckpointError, decode, restore, snapshot
from mock_paypal.payout_models import CreatePayout


async def prepare(app):
    store = app.state.store
    app.state.resource_service.seed({
        "balances":{"available":"1500.00", "withheld":"0.00", "currency_code":"USD"},
        "registered_receivers":["maker@example.test"],
        "captures":[{"id":"CAP1", "order_id":"ORDER1", "amount":"400.00", "create_time":"2026-11-02T09:00:00Z"}],
    })
    batch = app.state.payout_service.create(CreatePayout.model_validate({
        "sender_batch_header":{"sender_batch_id":"snapshot-roundtrip"},
        "items":[{"recipient_type":"EMAIL", "receiver":"maker@example.test",
                  "amount":{"value":"1.00", "currency":"USD"}, "sender_item_id":"item1"}],
    }))
    app.state.payout_service.remember_keys(batch)
    return store, batch


@pytest.mark.asyncio
async def test_snapshot_roundtrip_preserves_gets_random_ids_and_future_events():
    async def delivery(*_args):
        return 200

    app = create_app(control_enabled=True, webhook_delivery=delivery)
    store, batch = await prepare(app)
    token = store.issue_token()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost:8099",
        headers={"Authorization":"Bearer " + token},
    ) as client:
        await client.post("/v1/notifications/webhooks", json={"url":"http://localhost:8000/webhooks/paypal", "event_types":[{"name":"*"}]})
        dispute = await client.post("/_mock/disputes", json={
            "capture_id":"CAP1", "reason":"MERCHANDISE_OR_SERVICE_NOT_RECEIVED",
            "amount":"400.00", "messages":["Staged test dispute"],
        })
        assert dispute.status_code == 200
        before = json.loads(json.dumps(snapshot(store)))
        paths = [f"/v1/payments/payouts/{batch.id}",
                 f"/v1/payments/payouts-item/{batch.item_ids[0]}",
                 "/v2/payments/captures/CAP1", "/v1/reporting/balances",
                 "/v1/customer/disputes/" + dispute.json()["dispute_id"]]
        expected = [(await client.get(path)).json() for path in paths]
        next_id = store.new_id()
        await store.clock.advance(40)
        future = app.state.payout_service.item_doc(app.state.payout_service.get_item(batch.item_ids[0]), "http://localhost:8099", standalone=True)
        store.reset()
        restore(app, before)
        assert [(await client.get(path)).json() for path in paths] == expected
        assert store.new_id() == next_id
        await store.clock.advance(40)
        assert app.state.payout_service.item_doc(app.state.payout_service.get_item(batch.item_ids[0]), "http://localhost:8099", standalone=True) == future
        assert store.webhooks.deliveries
        assert all(item["status"] == 200 for item in store.webhooks.deliveries)


@pytest.mark.asyncio
async def test_pending_delivery_preserves_record_alias_and_signature_after_restore():
    app = create_app(control_enabled=True)
    store, _batch = await prepare(app)
    store.webhooks.registrations["WH-MOCK-TEST"] = {"url":"http://localhost:8000/webhooks/paypal", "event_types":[{"name":"*"}]}
    event = store.emit("CUSTOMER.DISPUTE.CREATED", {"dispute_id":"PP-D-1"})
    saved = json.loads(json.dumps(snapshot(store)))
    restore(app, saved)
    descriptor = next(e for e in store.clock._checkpoints.values() if e["kind"] == "deliver")
    assert descriptor["record"] is store.webhooks.events[event["id"]]
    header = descriptor["headers"]
    assert app.state.webhook_service.valid({
        "webhook_event":event, "webhook_id":"WH-MOCK-TEST",
        **{key.replace("paypal-", "").replace("-", "_"):value for key, value in header.items() if key.startswith("paypal-")},
    })
    assert store.clock.now + timedelta(seconds=5) == min(due for due, _, _ in store.clock._events)


@pytest.mark.asyncio
async def test_only_fixed_local_files_can_be_saved_or_loaded(tmp_path, monkeypatch):
    monkeypatch.setenv("COUNTERSIGN_MOCK_CHECKPOINT_DIR", str(tmp_path))
    monkeypatch.setenv("COUNTERSIGN_FRIDAY_BUILD", "1")
    app = create_app(control_enabled=True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 1200)), base_url="http://localhost") as client:
        assert (await client.post("/_mock/checkpoints/friday/save")).status_code == 200
        assert (tmp_path / "mock.json").is_file()
        for suffix, options in [("/load?file=other", {}), ("/load", {"json":{"data":"uploaded"}}), ("/other", {})]:
            assert (await client.post("/_mock/checkpoints/friday" + suffix, **options)).status_code == 422
        assert (await client.post("/_mock/checkpoints/friday/load")).status_code == 200
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("203.0.113.10", 1200)), base_url="http://localhost") as client:
        assert (await client.post("/_mock/checkpoints/friday/load")).status_code == 404


def test_non_quiescent_and_unknown_code_formats_fail_closed():
    store = create_app(control_enabled=True).state.store
    store.clock.schedule(1, lambda: None)
    with pytest.raises(CheckpointError, match="NOT_QUIESCENT"):
        snapshot(store)
    with pytest.raises(CheckpointError, match="TYPE_UNSUPPORTED"):
        decode({"root":{"ref":0}, "nodes":[{"type":"python.exec", "value":"unsafe"}]})
