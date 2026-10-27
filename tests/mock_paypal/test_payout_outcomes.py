"""M14 forced payout outcomes: items whose IDs a scenario cannot know in advance."""

import json

import pytest
from fastapi.testclient import TestClient
from test_payouts import PATH, advance, authenticated, body, create, item

from mock_paypal.app import create_app

OUTCOMES = "/_mock/payout-outcomes"


@pytest.fixture
def client():
    events = []

    async def delivery(url, headers, raw):
        events.append(raw)
        return 200

    app = create_app(control_enabled=True, webhook_delivery=delivery)
    app.state.store.payouts.available_minor = 150000
    app.state.store.payouts.registered_receivers.add("maker@example.com")
    with TestClient(app) as client:
        authenticated(client)
        client.post(
            "/v1/notifications/webhooks",
            json={"url": "http://localhost:8098/hook", "event_types": [{"name": "*"}]},
        )
        client.events = events
        yield client


def force(client, **rule):
    response = client.post(OUTCOMES, json={"receiver": "maker@example.com", **rule})
    assert response.status_code == 201
    return response.json()["id"]


def truth(client):
    return client.get("/_mock/ground-truth").json()


@pytest.mark.parametrize(
    "target,left,available",
    [
        ("ONHOLD", True, "1200.00"),
        ("BLOCKED", True, "1200.00"),
        ("FAILED", False, "1500.00"),
        ("UNCLAIMED", True, "1200.00"),
        ("SUCCESS", True, "1200.00"),
    ],
)
def test_forced_outcome_reuses_transition_accounting_and_is_synthesised(
    client, target, left, available
):
    force(client, to=target)
    batch = create(client)
    advance(client, 30)
    doc = item(client, batch)
    assert doc["transaction_status"] == target
    row = truth(client)["payout_items"][0]
    assert row["status"] == target and row["synthesised"] is True
    assert row["money_left_account"] is left
    assert truth(client)["balances"]["available"]["value"] == available


def test_forced_hold_emits_the_held_item_webhook(client):
    force(client, to="ONHOLD")
    create(client)
    advance(client, 60)
    assert any(b'"PAYMENT.PAYOUTS-ITEM.HELD"' in raw for raw in client.events)
    assert not any(b'"PAYMENT.PAYOUTS-ITEM.SUCCEEDED"' in raw for raw in client.events)


def test_forced_pending_keeps_item_and_batch_unfinished(client):
    force(client, to="PENDING")
    batch = create(client)
    advance(client, 86400)
    doc = client.get(f"{PATH}/{batch}").json()
    assert doc["batch_header"]["batch_status"] == "PROCESSING"
    assert doc["items"][0]["transaction_status"] == "PENDING"
    row = truth(client)["payout_items"][0]
    assert row["money_left_account"] and row["synthesised"]
    assert not any(b'"PAYMENT.PAYOUTSBATCH.SUCCESS"' in raw for raw in client.events)


def test_forced_failure_can_carry_recorded_style_errors(client):
    force(client, to="FAILED", errors={"name": "RISK_DECLINE", "message": "Synthesised"})
    batch = create(client)
    advance(client, 60)
    assert item(client, batch)["errors"]["name"] == "RISK_DECLINE"
    events = [json.loads(raw) for raw in client.events]
    failed = next(event for event in events if event["event_type"] == "PAYMENT.PAYOUTS-ITEM.FAILED")
    assert failed["resource"]["errors"] == item(client, batch)["errors"]


def test_nth_and_count_apply_per_receiver_only(client):
    force(client, to="BLOCKED", nth=2, count=1)
    client.app.state.store.payouts.registered_receivers.add("other@example.com")
    first = create(client, body("b1"))
    other = create(client, body("b2", receiver="other@example.com"))
    second = create(client, body("b3"))
    third = create(client, body("b4"))
    advance(client, 30)
    assert item(client, first)["transaction_status"] == "SUCCESS"
    assert item(client, other)["transaction_status"] == "SUCCESS"
    assert item(client, second)["transaction_status"] == "BLOCKED"
    assert item(client, third)["transaction_status"] == "SUCCESS"
    synthesised = {r["sender_batch_id"]: r["synthesised"] for r in truth(client)["payout_items"]}
    assert synthesised == {"b1": False, "b2": False, "b3": True, "b4": False}


def test_rules_apply_only_to_items_processed_after_the_rule(client):
    batch = create(client)
    advance(client, 30)
    force(client, to="FAILED")
    assert item(client, batch)["transaction_status"] == "SUCCESS"
    assert client.get(OUTCOMES).json()["items"][0]["used"] == 0


def test_validation_duplicates_listing_and_reset(client):
    for bad in [
        {"receiver": "maker@example.com", "to": "RETURNED"},
        {"receiver": "", "to": "FAILED"},
        {"receiver": "maker@example.com", "to": "FAILED", "nth": 0},
        {"receiver": "maker@example.com", "to": "FAILED", "unknown": 1},
        {"to": "FAILED"},
    ]:
        assert client.post(OUTCOMES, json=bad).status_code == 422
    force(client, id="fixed", to="FAILED")
    assert (
        client.post(
            OUTCOMES, json={"id": "fixed", "receiver": "x@example.com", "to": "FAILED"}
        ).status_code
        == 409
    )
    assert [r["id"] for r in client.get(OUTCOMES).json()["items"]] == ["fixed"]
    assert client.post("/_mock/reset", json={"seed": 17}).status_code == 200
    assert client.get(OUTCOMES).json()["items"] == []


def test_control_is_disabled_without_mock_control():
    with TestClient(create_app(control_enabled=False)) as client:
        response = client.post(OUTCOMES, json={"receiver": "a@example.com", "to": "FAILED"})
        assert response.status_code == 404
