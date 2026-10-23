import asyncio
import base64
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from mock_paypal.app import create_app
from mock_paypal.clock import START, MockClock, timestamp
from mock_paypal.store import MockStore

DEFAULTS = {
    "payout_processing_seconds": 30,
    "key_retention_days": 30,
    "unclaimed_counts_in_available": False,
    "dispute_hold": True,
    "evidence_allowed_stages": ["INQUIRY", "CHARGEBACK"],
    "balance_refresh_seconds": 10800,
    "transactions_lag_seconds": 10800,
    "webhook_delay_seconds": 5,
    "cancel_emits": ["CANCELED", "RETURNED"],
}
BASIC = {"Authorization": "Basic " + base64.b64encode(b"mock:dummy").decode()}


@pytest.fixture
def client():
    app = create_app(control_enabled=True)

    @app.get("/v1/test-protected")
    def protected():
        return {"ok": True}

    with TestClient(app) as client:
        yield client


def issue(client):
    response = client.post(
        "/v1/oauth2/token", headers=BASIC, data={"grant_type": "client_credentials"}
    )
    assert response.status_code == 200
    assert response.headers["paypal-debug-id"]
    return response.json()


def bearer(token):
    return {"Authorization": "Bearer " + token}


def assert_error(response, status):
    assert response.status_code == status
    body = response.json()
    assert set(body) == {
        "name",
        "message",
        "debug_id",
        "information_link",
        "details",
        "links",
    }
    assert body["debug_id"] == response.headers["paypal-debug-id"]
    assert isinstance(body["details"], list)
    assert isinstance(body["links"], list)


def test_oauth_and_bearer_expiry_use_mock_clock(client):
    body = issue(client)
    assert body["token_type"] == "Bearer"
    assert body["app_id"] == "APP-MOCK"
    assert body["expires_in"] == 32400
    assert body["access_token"].startswith("MOCK-")
    assert body["nonce"].startswith(timestamp(START))
    assert len(body["scope"].split()) == 4
    headers = bearer(body["access_token"])
    assert client.get("/v1/test-protected", headers=headers).json() == {"ok": True}
    client.post("/_mock/clock/advance", json={"seconds": 32399})
    assert client.get("/v1/test-protected", headers=headers).status_code == 200
    client.post("/_mock/clock/advance", json={"seconds": 1})
    expired = client.get("/v1/test-protected", headers=headers)
    assert expired.status_code == 401
    assert expired.json()["error"] == "invalid_token"
    assert expired.headers["paypal-debug-id"]


@pytest.mark.parametrize("authorization", [None, "Bearer fake", "Basic abc", "Bearer"])
def test_rejects_invalid_bearer(client, authorization):
    headers = {} if authorization is None else {"Authorization": authorization}
    response = client.get("/v1/test-protected", headers=headers)
    assert response.status_code == 401
    assert response.json()["error"] == "invalid_token"
    assert response.headers["paypal-debug-id"]


@pytest.mark.parametrize("authorization", ["Basic !!!", "Basic YTo=", "Bearer abc", ""])
def test_bad_basic_auth(client, authorization):
    response = client.post(
        "/v1/oauth2/token",
        headers={"Authorization": authorization},
        data={"grant_type": "client_credentials"},
    )
    assert response.status_code == 401
    assert response.json()["error"] == "invalid_client"
    assert response.headers["paypal-debug-id"]


def test_grant_and_content_type_validation(client):
    wrong = client.post(
        "/v1/oauth2/token", headers=BASIC, data={"grant_type": "password"}
    )
    assert wrong.status_code == 400
    assert wrong.json()["error"] == "unsupported_grant_type"
    assert_error(client.post("/v1/oauth2/token", headers=BASIC, json={}), 400)


def test_pending_scope_can_be_overridden():
    with TestClient(create_app(oauth_scope="mock-scope")) as client:
        assert issue(client)["scope"] == "mock-scope"


def test_config_defaults_and_partial_updates(client):
    assert client.get("/_mock/config").json() == DEFAULTS
    for key, value in DEFAULTS.items():
        if isinstance(value, bool):
            changed = not value
        elif isinstance(value, int):
            changed = value + 1
        else:
            changed = ["CUSTOM"]
        response = client.post("/_mock/config", json={key: changed})
        assert response.status_code == 200
        assert response.json()[key] == changed
    assert (
        client.post("/_mock/config", json={}).json()
        == client.get("/_mock/config").json()
    )


@pytest.mark.parametrize(
    "body",
    [
        {"unknown": 1},
        {"key_retention_days": 0},
        {"balance_refresh_seconds": 0},
        {"webhook_delay_seconds": -1},
        {"dispute_hold": "true"},
        {"payout_processing_seconds": True},
        {"payout_processing_seconds": 1.5},
        {"evidence_allowed_stages": "INQUIRY"},
        {"cancel_emits": [1]},
        {"transactions_lag_seconds": None},
    ],
)
def test_config_rejects_invalid_update_atomically(client, body):
    assert_error(client.post("/_mock/config", json=body), 422)
    assert client.get("/_mock/config").json() == DEFAULTS


def test_clock_controls_and_errors(client):
    assert client.get("/_mock/clock").json() == {"now": timestamp(START)}
    assert client.post("/_mock/clock/advance", json={"seconds": 3600}).json() == {
        "now": "2026-11-02T10:00:00Z"
    }
    response = client.post(
        "/_mock/clock/set", json={"now": "2026-11-02T12:00:00+01:00"}
    )
    assert response.json() == {"now": "2026-11-02T11:00:00Z"}
    for value in ["2026-11-02T10:00:00Z", "2026-11-02T12:00:00", "bad"]:
        assert_error(client.post("/_mock/clock/set", json={"now": value}), 422)
    for body in [{"seconds": -1}, {"seconds": True}, {"seconds": 0.5}, {}]:
        assert_error(client.post("/_mock/clock/advance", json=body), 422)
    assert client.get("/_mock/clock").json() == {"now": "2026-11-02T11:00:00Z"}


def test_clock_drains_nested_async_and_equal_time_events():
    async def run():
        clock = MockClock()
        observed = []

        async def first():
            observed.append(("first", clock.now))
            clock.schedule(2, lambda: observed.append(("nested", clock.now)))

        clock.schedule(5, first)
        clock.schedule(5, lambda: observed.append(("second", clock.now)))
        clock.schedule(20, lambda: observed.append(("future", clock.now)))
        assert observed == []
        await clock.advance(10)
        assert observed == [
            ("first", START + timedelta(seconds=5)),
            ("second", START + timedelta(seconds=5)),
            ("nested", START + timedelta(seconds=7)),
        ]
        assert clock.now == START + timedelta(seconds=10)
        await clock.advance(10)
        assert observed[-1] == ("future", START + timedelta(seconds=20))
        with pytest.raises(ValueError):
            clock.schedule(-1, lambda: None)

    asyncio.run(run())


def test_http_advance_waits_for_due_events(client):
    observed = []
    store = client.app.state.store
    store.clock.schedule(5, lambda: observed.append(timestamp(store.clock.now)))
    response = client.post("/_mock/clock/set", json={"now": "2026-11-02T09:00:10Z"})
    assert response.status_code == 200
    assert observed == ["2026-11-02T09:00:05Z"]


def test_reset_clears_all_state_and_reproduces_ids(client):
    store = client.app.state.store
    client.post("/_mock/reset", json={"seed": 42})
    first = store.new_id()
    token = issue(client)["access_token"]
    store.records["item"] = {"status": "PENDING"}
    observed = []
    store.clock.schedule(1, lambda: observed.append(True))
    client.post("/_mock/config", json={"dispute_hold": False})
    client.post("/_mock/reset", json={"seed": 42})
    assert store.new_id() == first
    assert store.records == {}
    assert client.get("/_mock/config").json() == DEFAULTS
    assert client.get("/_mock/clock").json() == {"now": timestamp(START)}
    assert client.get("/v1/test-protected", headers=bearer(token)).status_code == 401
    client.post("/_mock/clock/advance", json={"seconds": 10})
    assert observed == []
    client.post("/_mock/reset", json={"seed": 43})
    assert store.new_id() != first


def test_control_disabled_by_default_and_enabled_by_environment(monkeypatch):
    monkeypatch.delenv("MOCK_CONTROL", raising=False)
    with TestClient(create_app()) as client:
        assert_error(client.get("/_mock/clock"), 404)
        assert_error(client.post("/_mock/reset", json={"seed": 1}), 404)
    monkeypatch.setenv("MOCK_CONTROL", "1")
    with TestClient(create_app()) as client:
        assert client.get("/_mock/clock").status_code == 200


def test_validation_errors_do_not_echo_input(client):
    response = client.post("/_mock/reset", json={"seed": "sensitive-input"})
    assert_error(response, 422)
    assert "sensitive-input" not in response.text
    assert_error(
        client.post(
            "/_mock/reset", content="{", headers={"Content-Type": "application/json"}
        ),
        422,
    )
    token = issue(client)["access_token"]
    assert_error(client.get("/v1/missing", headers=bearer(token)), 404)
    assert_error(client.get("/v1/oauth2/token"), 405)


def test_apps_do_not_share_state():
    one, two = MockStore(), MockStore()
    one.records["item"] = {}
    one.config.cancel_emits.append("CUSTOM")
    assert two.records == {}
    assert two.config.cancel_emits == ["CANCELED", "RETURNED"]


def test_unexpected_errors_keep_envelope_and_hide_exception():
    app = create_app(control_enabled=True)

    @app.get("/_mock/test-error")
    def fail():
        raise RuntimeError("sensitive-internal-value")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/_mock/test-error")
        assert_error(response, 500)
        assert "sensitive-internal-value" not in response.text
