"""Four idempotency rules and the retention boundary, through the HTTP API."""

import asyncio
from copy import deepcopy

import httpx
import pytest
from fastapi.testclient import TestClient
from test_payouts import PATH, advance, authenticated, body, check_error, create, item

from mock_paypal.app import create_app
from mock_paypal.payouts import PayoutBehavior


@pytest.fixture
def client():
    app = create_app(control_enabled=True)
    app.state.store.payouts.available_minor = 150000
    app.state.store.payouts.registered_receivers.add("maker@example.com")
    with TestClient(app) as client:
        authenticated(client)
        yield client


def test_rule_two_exact_replay_uses_current_batch_and_only_debits_once(client):
    headers = {"PayPal-Request-Id": "request-1"}
    batch_id = create(client, headers=headers)
    advance(client, 30)
    response = client.post(PATH, json=body(), headers=headers)
    assert response.status_code == 201
    assert response.json()["batch_header"]["payout_batch_id"] == batch_id
    assert response.json()["batch_header"]["batch_status"] == "SUCCESS"
    assert "items" not in response.json()
    assert len(client.app.state.store.payouts.items) == 1
    assert client.app.state.store.payouts.available_minor == 120000


def test_rule_two_original_wins_with_changed_sender_amount_and_receiver(client):
    headers = {"PayPal-Request-Id": "request-1"}
    batch_id = create(client, headers=headers)
    changed = body("different-batch", "other@example.com", "9999.00")
    response = client.post(PATH, json=changed, headers=headers)
    assert response.status_code == 201
    assert response.json()["batch_header"]["payout_batch_id"] == batch_id
    assert item(client, batch_id)["payout_item"] == body()["items"][0]
    assert client.app.state.store.payouts.available_minor == 120000


def test_rule_two_precedes_new_body_validation_and_sender_collision(client):
    create(client, headers={"PayPal-Request-Id": "first"})
    second = create(client, body("batch-2"), headers={"PayPal-Request-Id": "second"})
    response = client.post(PATH, json=body(), headers={"PayPal-Request-Id": "second"})
    assert response.status_code == 201
    assert response.json()["batch_header"]["payout_batch_id"] == second
    response = client.post(
        PATH,
        content="{",
        headers={"PayPal-Request-Id": "second", "Content-Type": "application/json"},
    )
    assert response.status_code == 201
    assert len(client.app.state.store.payouts.items) == 2


@pytest.mark.parametrize("request_id", [None, "new-request"])
def test_rule_three_duplicate_sender_links_original_batch(client, request_id):
    batch_id = create(client, headers={"PayPal-Request-Id": "original-request"})
    headers = {} if request_id is None else {"PayPal-Request-Id": request_id}
    doc = check_error(
        client.post(PATH, json=body(), headers=headers), 400, "USER_BUSINESS_ERROR"
    )
    assert doc["details"] == [
        {
            "field": "sender_batch_id",
            "value": "batch-1",
            "location": "body",
            "issue": "DUPLICATE_SENDER_BATCH_ID",
        }
    ]
    linked = client.get(doc["links"][0]["href"])
    assert linked.status_code == 200
    assert linked.json()["batch_header"]["payout_batch_id"] == batch_id
    assert len(client.app.state.store.payouts.items) == 1


def test_rule_three_also_works_without_any_request_id(client):
    create(client)
    check_error(client.post(PATH, json=body()), 400, "USER_BUSINESS_ERROR")
    assert len(client.app.state.store.payouts.items) == 1


def test_rule_four_new_keys_create_distinct_payouts(client):
    first = create(client, headers={"PayPal-Request-Id": "one"})
    second = create(client, body("batch-2"), headers={"PayPal-Request-Id": "two"})
    assert first != second
    assert len(client.app.state.store.payouts.items) == 2
    assert client.app.state.store.payouts.available_minor == 90000


@pytest.mark.parametrize("use_request_id", [False, True])
def test_keys_expire_exactly_at_30_days_without_extending_on_replay(
    client, use_request_id
):
    headers = {"PayPal-Request-Id": "one"} if use_request_id else {}
    first = create(client, headers=headers)
    advance(client, 30 * 86400 - 1)
    response = client.post(PATH, json=body(), headers=headers)
    assert response.status_code == (201 if use_request_id else 400)
    advance(client, 1)
    second = create(client, headers=headers)
    assert first != second
    assert len(client.app.state.store.payouts.items) == 2
    assert client.app.state.store.payouts.available_minor == 90000
    # Historical batches remain readable after their deduplication keys expire.
    assert client.get(f"{PATH}/{first}").status_code == 200


def test_configured_retention_and_reset(client):
    client.post("/_mock/config", json={"key_retention_days": 1})
    first = create(client, headers={"PayPal-Request-Id": "one"})
    advance(client, 86400)
    second = create(client, headers={"PayPal-Request-Id": "one"})
    assert second != first
    client.post("/_mock/reset", json={"seed": 17})
    state = client.app.state.store.payouts
    assert state.request_keys == {}
    assert state.sender_keys == {}
    state.available_minor = 30000
    authenticated(client)
    create(client, headers={"PayPal-Request-Id": "one"})


def test_rejected_request_does_not_poison_keys(client):
    payload = body(value="1500.01")
    headers = {"PayPal-Request-Id": "retry"}
    check_error(
        client.post(PATH, json=payload, headers=headers), 422, "INSUFFICIENT_FUNDS"
    )
    create(client, body(value="1.00"), headers=headers)
    assert len(client.app.state.store.payouts.items) == 1
    assert client.app.state.store.payouts.available_minor == 149900


def test_pending_d6_behavior_is_configurable():
    app = create_app(
        control_enabled=True,
        payout_behavior=PayoutBehavior(
            replay_current_header=False,
            replay_original_body_wins=False,
            duplicate_batch_status=409,
            duplicate_batch_name="CUSTOM_DUPLICATE",
            duplicate_batch_issue="CUSTOM_ISSUE",
        ),
    )
    app.state.store.payouts.available_minor = 150000
    with TestClient(app) as client:
        authenticated(client)
        create(client, headers={"PayPal-Request-Id": "one"})
        advance(client, 30)
        response = client.post(PATH, json=body(), headers={"PayPal-Request-Id": "one"})
        assert response.json()["batch_header"]["batch_status"] == "PENDING"
        check_error(
            client.post(
                PATH, json=body(value="1.00"), headers={"PayPal-Request-Id": "one"}
            ),
            400,
            "INVALID_REQUEST",
        )
        doc = check_error(client.post(PATH, json=body()), 409, "CUSTOM_DUPLICATE")
        assert doc["details"][0]["issue"] == "CUSTOM_ISSUE"


def test_rule_one_concurrent_duplicates_one_201_one_409():
    async def run():
        app = create_app(control_enabled=True)
        app.state.store.payouts.available_minor = 150000
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://localhost:8099"
        ) as client:
            client.headers["Authorization"] = "Bearer " + app.state.store.issue_token()
            for index in range(5):
                payload = body(f"batch-{index}", value="1.00")
                headers = {"PayPal-Request-Id": f"request-{index}"}
                responses = await asyncio.gather(
                    client.post(PATH, json=payload, headers=headers),
                    client.post(PATH, json=deepcopy(payload), headers=headers),
                )
                assert sorted(response.status_code for response in responses) == [
                    201,
                    409,
                ]
                loser = next(
                    response for response in responses if response.status_code == 409
                )
                check_error(loser, 409, "DUPLICATE_REQUEST_ID")
                replay = await client.post(PATH, json=payload, headers=headers)
                assert replay.status_code == 201
            assert len(app.state.store.payouts.items) == 5
            assert app.state.store.payouts.available_minor == 149500

    asyncio.run(run())


@pytest.mark.parametrize("same_sender", [True, False])
def test_concurrent_funding_and_sender_checks_are_atomic(same_sender):
    async def run():
        app = create_app(control_enabled=True)
        app.state.store.payouts.available_minor = 30000
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://localhost:8099"
        ) as client:
            client.headers["Authorization"] = "Bearer " + app.state.store.issue_token()
            responses = await asyncio.gather(
                client.post(PATH, json=body(), headers={"PayPal-Request-Id": "one"}),
                client.post(
                    PATH,
                    json=body("batch-1" if same_sender else "batch-2"),
                    headers={"PayPal-Request-Id": "two"},
                ),
            )
            assert sorted(response.status_code for response in responses) == [
                201,
                400 if same_sender else 422,
            ]
            assert len(app.state.store.payouts.items) == 1
            assert app.state.store.payouts.available_minor == 0

    asyncio.run(run())
