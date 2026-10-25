"""D6 sender-batch duplicates and retention, through the HTTP API."""

import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient
from test_payouts import PATH, advance, authenticated, body, check_error, create, item

from mock_paypal.app import create_app


@pytest.fixture
def client():
    app = create_app(control_enabled=True)
    app.state.store.payouts.available_minor = 150000
    app.state.store.payouts.registered_receivers.add("maker@example.com")
    with TestClient(app) as client:
        authenticated(client)
        yield client


def duplicate(response, batch_id, base="http://testserver"):
    doc = check_error(response, 400, "USER_BUSINESS_ERROR")
    assert doc["message"] == "User business error."
    assert (
        doc["information_link"]
        == "https://developer.paypal.com/docs/api/payments.payouts-batch/#errors"
    )
    assert doc["links"] == []
    assert doc["debug_id"]
    assert doc["details"] == [
        {
            "field": "SENDER_BATCH_ID",
            "location": "body",
            "issue": "Batch with given sender_batch_id already exists",
            "link": [
                {
                    "href": f"{base}{PATH}/{batch_id}",
                    "rel": "self",
                    "method": "GET",
                    "encType": "application/json",
                }
            ],
        }
    ]
    return doc["details"][0]["link"][0]["href"]


@pytest.mark.parametrize("request_id", [None, "original", "new"])
@pytest.mark.parametrize("changed", [False, True])
def test_every_duplicate_links_original_without_changing_payment(
    client, request_id, changed
):
    batch_id = create(client, headers={"PayPal-Request-Id": "original"})
    advance(client, 30)
    payload = body(receiver="other@example.com", value="9999.00") if changed else body()
    headers = {"PayPal-Request-Id": request_id} if request_id else {}
    href = duplicate(client.post(PATH, json=payload, headers=headers), batch_id)
    linked = client.get(href)
    assert linked.status_code == 200
    assert linked.json()["batch_header"]["payout_batch_id"] == batch_id
    assert linked.json()["batch_header"]["batch_status"] == "SUCCESS"
    assert item(client, batch_id)["payout_item"] == body()["items"][0]
    truth = client.get("/_mock/ground-truth").json()
    assert len(truth["payout_items"]) == 1
    assert truth["balances"]["available"]["value"] == "1200.00"


def test_duplicates_without_any_request_id(client):
    batch_id = create(client)
    duplicate(client.post(PATH, json=body()), batch_id)


def test_request_id_alone_does_not_deduplicate_new_sender(client):
    headers = {"PayPal-Request-Id": "same"}
    first = create(client, headers=headers)
    second = create(client, body("batch-2"), headers=headers)
    assert first != second
    duplicate(client.post(PATH, json=body(), headers=headers), first)
    assert len(client.app.state.store.payouts.items) == 2
    assert client.app.state.store.payouts.available_minor == 90000


def test_reused_request_id_does_not_skip_body_validation(client):
    create(client, headers={"PayPal-Request-Id": "same"})
    check_error(
        client.post(
            PATH,
            content="{",
            headers={
                "PayPal-Request-Id": "same",
                "Content-Type": "application/json",
            },
        ),
        422,
        "INVALID_REQUEST",
    )
    assert len(client.app.state.store.payouts.items) == 1


def test_duplicate_link_host_config_and_reset(client):
    batch_id = create(client)
    assert client.get("/_mock/config").json()["duplicate_link_host"] == "mock"
    assert (
        client.post(
            "/_mock/config", json={"duplicate_link_host": "sandbox"}
        ).status_code
        == 200
    )
    duplicate(
        client.post(PATH, json=body()), batch_id, "https://api.sandbox.paypal.com"
    )
    assert (
        client.post(
            "/_mock/config", json={"duplicate_link_host": "elsewhere"}
        ).status_code
        == 422
    )
    assert (
        client.post("/_mock/config", json={"duplicate_link_host": "mock"}).status_code
        == 200
    )
    assert (
        client.get(duplicate(client.post(PATH, json=body()), batch_id)).status_code
        == 200
    )
    client.post("/_mock/config", json={"duplicate_link_host": "sandbox"})
    client.post("/_mock/reset", json={"seed": 17})
    assert client.get("/_mock/config").json()["duplicate_link_host"] == "mock"
    assert client.app.state.store.payouts.sender_keys == {}


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
    duplicate(response, first)
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


@pytest.mark.parametrize("same_request_id", [False, True])
def test_concurrent_duplicates_one_201_one_400(same_request_id):
    async def run():
        app = create_app(control_enabled=True)
        app.state.store.payouts.available_minor = 150000
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://localhost:8099"
        ) as client:
            client.headers["Authorization"] = "Bearer " + app.state.store.issue_token()
            for index in range(5):
                payload = body(f"batch-{index}", value="1.00")
                responses = await asyncio.gather(
                    *[
                        client.post(
                            PATH,
                            json=payload,
                            headers={
                                "PayPal-Request-Id": f"request-{index}-{0 if same_request_id else n}"
                            },
                        )
                        for n in range(2)
                    ]
                )
                assert sorted(r.status_code for r in responses) == [201, 400]
                winner = next(r for r in responses if r.status_code == 201)
                batch_id = winner.json()["batch_header"]["payout_batch_id"]
                loser = next(r for r in responses if r.status_code == 400)
                href = duplicate(loser, batch_id, "http://localhost:8099")
                assert (await client.get(href)).json()["batch_header"][
                    "payout_batch_id"
                ] == batch_id
                duplicate(
                    await client.post(PATH, json=payload),
                    batch_id,
                    "http://localhost:8099",
                )
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
