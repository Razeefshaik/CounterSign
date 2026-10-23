import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient
from test_payouts import PATH, advance, authenticated, body, create, item

from mock_paypal.app import create_app
from mock_paypal.faults import NEGATIVE_VALUES


@pytest.fixture
def client():
    app = create_app(control_enabled=True)
    app.state.store.payouts.available_minor = 150000
    with TestClient(app) as client:
        authenticated(client)
        yield client


def inject(client, action, **match):
    response = client.post(
        "/_mock/faults",
        json={
            "match": {"method": "POST", "path": "^" + PATH + "$", **match},
            "action": action,
        },
    )
    assert response.status_code == 201
    return response.json()["id"]


@pytest.mark.parametrize("kind", ["drop_response", "connection_reset"])
@pytest.mark.parametrize("after", [True, False])
def test_connection_loss_and_same_key_retry(client, kind, after):
    inject(client, {"type": kind, "after_commit": after})
    with pytest.raises(ConnectionResetError):
        client.post(PATH, json=body(), headers={"PayPal-Request-Id": "stable"})
    truth = client.get("/_mock/ground-truth").json()
    assert len(truth["payout_items"]) == int(after)
    create(client, headers={"PayPal-Request-Id": "stable"})
    truth = client.get("/_mock/ground-truth").json()
    assert len(truth["payout_items"]) == 1
    assert truth["payout_items"][0]["money_left_account"]
    assert truth["balances"]["available"]["value"] == "1200.00"


@pytest.mark.parametrize("kind", ["delay", "timeout"])
@pytest.mark.parametrize("after", [True, False])
def test_waits_use_mock_clock(kind, after):
    async def run():
        app = create_app(control_enabled=True)
        app.state.store.payouts.available_minor = 150000
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://localhost:8099"
        ) as client:
            client.headers["Authorization"] = "Bearer " + app.state.store.issue_token()
            await client.post(
                "/_mock/faults",
                json={
                    "match": {"method": "POST", "path": "^" + PATH + "$"},
                    "action": {"type": kind, "seconds": 30, "after_commit": after},
                },
            )
            task = asyncio.create_task(client.post(PATH, json=body()))
            # Synchronize on the scheduler, never a wall-clock delay.
            for _ in range(50):
                if app.state.store.clock._events:
                    break
                await asyncio.sleep(0)
            await client.post("/_mock/clock/advance", json={"seconds": 29})
            assert not task.done()
            await client.post("/_mock/clock/advance", json={"seconds": 1})
            if kind == "timeout":
                with pytest.raises(ConnectionResetError):
                    await task
                assert len(app.state.store.payouts.items) == int(after)
            else:
                assert (await task).status_code == 201
                assert len(app.state.store.payouts.items) == 1

    asyncio.run(run())


@pytest.mark.parametrize("after", [True, False])
def test_http_error_can_commit_or_skip(client, after):
    inject(client, {"type": "http_error", "status": 503, "after_commit": after})
    response = client.post(PATH, json=body())
    assert response.status_code == 503
    assert response.json()["debug_id"] == response.headers["paypal-debug-id"]
    assert len(client.get("/_mock/ground-truth").json()["payout_items"]) == int(after)


def test_nth_count_delete_and_request_audit(client):
    id = inject(client, {"type": "http_error"}, nth=2, count=2)
    assert len(client.get("/_mock/faults").json()["items"]) == 1
    assert client.post(PATH, json=body()).status_code == 201
    for _ in range(2):
        assert client.post(PATH, json=body("second")).status_code == 500
    assert client.post(PATH, json=body("second")).status_code == 201
    requests = client.get("/_mock/ground-truth").json()["requests"]
    assert sum(r["fault_id"] == id for r in requests) == 2
    assert all("authorization" not in r for r in requests)
    assert client.delete("/_mock/faults/" + id).status_code == 200
    assert client.delete("/_mock/faults/" + id).status_code == 404


@pytest.mark.parametrize("value", list(NEGATIVE_VALUES))
def test_all_d7_codes(client, value):
    inject(client, {"type": "test_value", "value": value})
    response = client.post(PATH, json=body())
    code, name = NEGATIVE_VALUES[value]
    assert response.status_code == code
    doc = response.json()
    if value.startswith("ERRPYOB"):
        assert doc["batch_header"]["batch_status"] == name
    elif value in {"ERRPYO015", "ERRPYO047", "ERRPYO052"}:
        assert doc["errors"]["name"] == name
    else:
        assert doc["name"] == name


def test_claim_race_and_synthetic_transitions(client):
    batch = create(client)
    advance(client, 30)
    id = item(client, batch)["payout_item_id"]
    path = "/_mock/payout-items/" + id
    assert (
        client.post(path + "/claim", json={"when": "during_next_cancel"}).status_code
        == 200
    )
    assert client.post("/v1/payments/payouts-item/" + id + "/cancel").status_code == 400
    assert item(client, batch)["transaction_status"] == "SUCCESS"
    for state in ["ONHOLD", "BLOCKED", "FAILED", "PENDING", "REFUNDED", "REVERSED"]:
        assert (
            client.post(
                path + "/transition", json={"to": state, "emit_webhook": False}
            ).status_code
            == 200
        )
    assert client.get("/_mock/ground-truth").json()["payout_items"][0]["synthesised"]
    assert (
        client.get("/_mock/ground-truth").json()["balances"]["available"]["value"]
        == "1500.00"
    )


def test_invalid_fault_and_control_gate(client):
    assert (
        client.post(
            "/_mock/faults",
            json={
                "match": {"method": "POST", "path": "["},
                "action": {"type": "delay"},
            },
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/_mock/faults",
            json={
                "match": {"method": "POST", "path": ".*"},
                "action": {"type": "test_value", "value": "bad"},
            },
        ).status_code
        == 422
    )
    with TestClient(create_app(control_enabled=False)) as off:
        assert off.get("/_mock/ground-truth").status_code == 404
