"""M02 acceptance tests. Fake data only; no network or credentials."""

from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from mock_paypal.app import create_app
from mock_paypal.payouts import PayoutBehavior

PATH = "/v1/payments/payouts"


def body(batch="batch-1", receiver="maker@example.com", value="300.00"):
    return {
        "sender_batch_header": {
            "sender_batch_id": batch,
            "email_subject": "Maker payout",
        },
        "items": [
            {
                "recipient_type": "EMAIL",
                "receiver": receiver,
                "amount": {"value": value, "currency": "USD"},
                "sender_item_id": batch + "-i1",
                "note": "Mock weekly payment",
            }
        ],
    }


def authenticated(client):
    # Issue directly to avoid exposing even dummy auth values in failure output.
    client.headers["Authorization"] = "Bearer " + client.app.state.store.issue_token()


@pytest.fixture
def client():
    app = create_app(control_enabled=True)
    app.state.store.payouts.available_minor = 150000
    app.state.store.payouts.registered_receivers.add("maker@example.com")
    with TestClient(app) as client:
        authenticated(client)
        yield client


def advance(client, seconds):
    response = client.post("/_mock/clock/advance", json={"seconds": seconds})
    assert response.status_code == 200
    authenticated(client)  # Tokens intentionally expire during long mock advances.


def create(client, payload=None, **kwargs):
    response = client.post(PATH, json=payload or body(), **kwargs)
    assert response.status_code == 201
    return response.json()["batch_header"]["payout_batch_id"]


def item(client, batch_id):
    return client.get(f"{PATH}/{batch_id}").json()["items"][0]


def check_error(response, status, name):
    assert response.status_code == status
    doc = response.json()
    assert doc["name"] == name
    assert doc["debug_id"] == response.headers["paypal-debug-id"]
    assert set(doc) == {
        "name",
        "message",
        "debug_id",
        "information_link",
        "details",
        "links",
    }
    return doc


def test_create_shape_and_success_at_processing_deadline(client):
    response = client.post(PATH, json=body())
    assert response.status_code == 201
    doc = response.json()
    assert set(doc) == {"batch_header", "links"}
    assert doc["batch_header"]["batch_status"] == "PENDING"
    batch_id = doc["batch_header"]["payout_batch_id"]
    batch = client.get(doc["links"][0]["href"]).json()
    assert batch["batch_header"]["amount"] == {"value": "300.00", "currency": "USD"}
    payout = batch["items"][0]
    assert payout["transaction_status"] == "PENDING"
    assert "errors" not in payout
    assert client.app.state.store.payouts.available_minor == 120000
    advance(client, 29)
    assert item(client, batch_id)["transaction_status"] == "PENDING"
    advance(client, 1)
    payout = item(client, batch_id)
    assert payout["transaction_status"] == "SUCCESS"
    standalone = client.get(payout["links"][0]["href"]).json()
    assert standalone["sender_batch_id"] == "batch-1"
    assert standalone["time_processed"] == "2026-11-02T09:00:30Z"
    batch = client.get(f"{PATH}/{batch_id}").json()
    assert batch["batch_header"]["batch_status"] == "SUCCESS"
    assert batch["batch_header"]["time_completed"] == standalone["time_processed"]
    assert client.app.state.store.payouts.available_minor == 120000


def test_unregistered_cancel_and_no_double_credit(client):
    batch_id = create(client, body(receiver="unregistered@example.com"))
    advance(client, 30)
    payout = item(client, batch_id)
    assert payout["transaction_status"] == "UNCLAIMED"
    assert payout["errors"]["name"] == "RECEIVER_UNREGISTERED"
    path = f"/v1/payments/payouts-item/{payout['payout_item_id']}/cancel"
    response = client.post(path)
    assert response.status_code == 200
    assert response.json()["transaction_status"] == "RETURNED"
    assert "errors" not in response.json()
    assert client.app.state.store.payouts.available_minor == 150000
    check_error(client.post(path), 400, "ITEM_ALREADY_CANCELLED")
    advance(client, 31 * 86400)
    assert client.app.state.store.payouts.available_minor == 150000


def test_unclaimed_automatically_returns_after_30_days_in_state(client):
    batch_id = create(client, body(receiver="unregistered@example.com"))
    advance(client, 30)
    advance(client, 30 * 86400 - 1)
    assert item(client, batch_id)["transaction_status"] == "UNCLAIMED"
    advance(client, 1)
    assert item(client, batch_id)["transaction_status"] == "RETURNED"
    assert client.app.state.store.payouts.available_minor == 150000


@pytest.mark.parametrize("seconds", [0, 30])
def test_cancel_pending_or_success_rejected(client, seconds):
    batch_id = create(client)
    advance(client, seconds)
    payout = item(client, batch_id)
    check_error(
        client.post(f"/v1/payments/payouts-item/{payout['payout_item_id']}/cancel"),
        400,
        "ITEM_INCORRECT_STATUS",
    )
    assert client.app.state.store.payouts.available_minor == 120000


def test_unclaimed_available_switch_and_claim_accounting(client):
    batch_id = create(client, body(receiver="unregistered@example.com"))
    advance(client, 30)
    assert client.app.state.store.payouts.available_minor == 120000
    client.post("/_mock/config", json={"unclaimed_counts_in_available": True})
    assert client.app.state.store.payouts.available_minor == 150000
    client.post("/_mock/config", json={"unclaimed_counts_in_available": False})
    assert client.app.state.store.payouts.available_minor == 120000
    client.post("/_mock/config", json={"unclaimed_counts_in_available": True})
    payout = client.app.state.payout_service.get_item(
        item(client, batch_id)["payout_item_id"]
    )
    client.app.state.payout_service.transition(payout, "SUCCESS")
    assert client.app.state.store.payouts.available_minor == 120000
    advance(client, 31 * 86400)
    assert item(client, batch_id)["transaction_status"] == "SUCCESS"
    assert client.app.state.store.payouts.available_minor == 120000


@pytest.mark.parametrize("target", ["FAILED", "RETURNED", "REFUNDED", "REVERSED"])
def test_crediting_states_only_return_money_once(client, target):
    batch_id = create(client)
    payout = client.app.state.payout_service.get_item(
        item(client, batch_id)["payout_item_id"]
    )
    service = client.app.state.payout_service
    service.transition(payout, target, synthesised=True)
    assert payout.synthesised
    assert client.app.state.store.payouts.available_minor == 150000
    service.transition(payout, target, synthesised=True)
    service.transition(payout, "RETURNED")
    assert client.app.state.store.payouts.available_minor == 150000
    advance(client, 30)
    assert payout.status == "RETURNED"


def test_insufficient_balance_is_atomic(client):
    check_error(
        client.post(PATH, json=body(value="1500.01")), 422, "INSUFFICIENT_FUNDS"
    )
    assert client.app.state.store.payouts.available_minor == 150000
    assert not client.app.state.store.payouts.items
    assert not client.app.state.store.payouts.batches
    assert client.app.state.store.payouts.withheld_minor == 0


@pytest.mark.parametrize(
    "value", ["0.00", "-1.00", "1", "1.1", "1.001", "1e2", "NaN", 1.25, True]
)
def test_invalid_amounts_do_not_mutate_state(client, value):
    check_error(client.post(PATH, json=body(value=value)), 422, "INVALID_REQUEST")
    assert not client.app.state.store.payouts.items
    assert client.app.state.store.payouts.available_minor == 150000


def test_exact_integer_arithmetic_above_float_precision(client):
    value = "90071992547409.93"
    client.app.state.store.payouts.available_minor = 9007199254740994
    batch_id = create(client, body(value=value))
    assert client.app.state.store.payouts.available_minor == 1
    assert item(client, batch_id)["payout_item"]["amount"]["value"] == value


def test_request_validation_and_batch_limits(client):
    for payload in [
        {},
        {"sender_batch_header": {}, "items": []},
        {**body(), "items": []},
        {**body(), "items": body()["items"] * 15001},
    ]:
        check_error(client.post(PATH, json=payload), 422, "INVALID_REQUEST")
    for change in [
        {"recipient_type": "PHONE"},
        {"receiver": ""},
        {"sender_item_id": ""},
    ]:
        payload = body()
        payload["items"][0].update(change)
        check_error(client.post(PATH, json=payload), 422, "INVALID_REQUEST")
    payload = body()
    payload["items"][0]["amount"]["currency"] = "EUR"
    check_error(client.post(PATH, json=payload), 422, "INVALID_REQUEST")
    check_error(
        client.post(PATH, content="{", headers={"Content-Type": "application/json"}),
        422,
        "INVALID_REQUEST",
    )
    assert not client.app.state.store.payouts.items


def test_multi_item_batch_pagination(client):
    payload = body(value="0.01")
    payload["items"] = [deepcopy(payload["items"][0]) for _ in range(3)]
    for i, entry in enumerate(payload["items"]):
        entry["sender_item_id"] = f"item-{i}"
    batch_id = create(client, payload)
    first = client.get(f"{PATH}/{batch_id}?page_size=2&total_required=true").json()
    assert first["total_items"] == 3
    assert first["total_pages"] == 2
    assert len(first["items"]) == 2
    next_link = next(link["href"] for link in first["links"] if link["rel"] == "next")
    second = client.get(next_link).json()
    assert len(second["items"]) == 1
    assert second["items"][0]["payout_item_id"] != first["items"][0]["payout_item_id"]
    assert first["batch_header"]["amount"]["value"] == "0.03"
    assert client.app.state.store.payouts.available_minor == 149997
    check_error(client.get(f"{PATH}/{batch_id}?page=0"), 422, "INVALID_REQUEST")


def test_15000_items_are_allowed(client):
    payload = body(value="0.01")
    payload["items"] = [
        {**payload["items"][0], "sender_item_id": f"i-{i}"} for i in range(15000)
    ]
    create(client, payload)
    assert len(client.app.state.store.payouts.items) == 15000
    assert client.app.state.store.payouts.available_minor == 135000


def test_missing_resources_and_auth(client):
    check_error(client.get(PATH + "/missing"), 404, "RESOURCE_NOT_FOUND")
    check_error(
        client.get("/v1/payments/payouts-item/missing"), 404, "RESOURCE_NOT_FOUND"
    )
    check_error(
        client.post("/v1/payments/payouts-item/missing/cancel"),
        404,
        "RESOURCE_NOT_FOUND",
    )
    client.headers.pop("Authorization")
    assert client.post(PATH, json=body()).status_code == 401


def test_reset_removes_payouts_and_timers(client):
    create(client)
    client.post("/_mock/reset", json={"seed": 17})
    assert not client.app.state.store.payouts.items
    assert client.app.state.store.payouts.available_minor == 0
    advance(client, 100)
    assert not client.app.state.store.payouts.items


def test_pending_response_switches():
    app = create_app(
        control_enabled=True,
        payout_behavior=PayoutBehavior(
            create_includes_items=True, insufficient_funds_status=400
        ),
    )
    with TestClient(app) as client:
        authenticated(client)
        check_error(client.post(PATH, json=body()), 400, "INSUFFICIENT_FUNDS")
        app.state.store.payouts.available_minor = 30000
        response = client.post(PATH, json=body())
        assert response.status_code == 201
        assert len(response.json()["items"]) == 1
