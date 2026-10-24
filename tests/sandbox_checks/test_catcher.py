import asyncio
import dataclasses
import json
import time
from datetime import UTC, datetime

import httpx
import pytest
from fastapi.testclient import TestClient

from scripts.sandbox_checks import register_webhook
from scripts.sandbox_checks.catcher import WEBHOOK_PATH, WebhookStore, create_app
from scripts.sandbox_checks.env import SandboxSettings
from scripts.sandbox_checks.recorder import Recorder
from scripts.sandbox_checks.redact import Redactor

NOW = datetime(2026, 10, 24, 9, 30, 15, 123456, tzinfo=UTC)
MAKER_EMAIL = "maker-a@fake.example"

# Odd spacing, key order and a non-ASCII note: a re-serialised copy would not match.
EVENT_BYTES = (
    '{ "id":"WH-FAKE-0001",  "event_type":"PAYMENT.PAYOUTS-ITEM.UNCLAIMED",\n'
    '"create_time":"2026-10-24T09:30:00Z","resource_type":"payouts_item",'
    '"resource":{"payout_item":{"receiver":"' + MAKER_EMAIL + '","note":"semana ✓"}}}'
).encode("utf-8")
PAYPAL_HEADERS = {
    "Content-Type": "application/json",
    "PAYPAL-TRANSMISSION-ID": "fake-transmission-1",
    "PAYPAL-TRANSMISSION-TIME": "2026-10-24T09:30:01Z",
    "PAYPAL-TRANSMISSION-SIG": "fake-signature==",
    "PAYPAL-CERT-URL": "https://api.sandbox.paypal.com/v1/notifications/certs/CERT-fake",
    "PAYPAL-AUTH-ALGO": "SHA256withRSA",
}


@pytest.fixture
def settings():
    return SandboxSettings(
        client_id="FAKE-CLIENT-ID", client_secret="FAKE-CLIENT-SECRET", maker_a_email=MAKER_EMAIL
    )


@pytest.fixture
def store(tmp_path, settings):
    return WebhookStore(redactor=Redactor.from_settings(settings), root=tmp_path, clock=lambda: NOW)


@pytest.fixture
def client(store):
    with TestClient(create_app(store)) as test_client:
        yield test_client


def test_stores_exact_bytes_headers_and_received_at(client, store):
    started = time.monotonic()
    response = client.post(WEBHOOK_PATH, content=EVENT_BYTES, headers=PAYPAL_HEADERS)

    assert response.status_code == 200
    assert time.monotonic() - started < 1.0
    [public_file] = list(store.public_dir.glob("*.json"))
    raw_file = store.root / json.loads(public_file.read_text(encoding="utf-8"))["raw_file"]
    assert raw_file.read_bytes() == EVENT_BYTES
    raw_headers = json.loads(raw_file.with_name(raw_file.stem + ".headers.json").read_text())
    assert raw_headers["paypal-transmission-sig"] == "fake-signature=="
    public = json.loads(public_file.read_text(encoding="utf-8"))
    assert public["received_at"] == "2026-10-24T09:30:15.123456Z"
    assert public["event_id"] == "WH-FAKE-0001"
    assert public["event_type"] == "PAYMENT.PAYOUTS-ITEM.UNCLAIMED"
    assert public["create_time"] == "2026-10-24T09:30:00Z"
    assert public["size"] == len(EVENT_BYTES)
    assert public["headers"]["paypal-transmission-id"] == "fake-transmission-1"


def test_public_copy_is_redacted_and_raw_copy_stays_under_raw(client, store):
    client.post(WEBHOOK_PATH, content=EVENT_BYTES, headers=PAYPAL_HEADERS)

    [public_file] = list(store.public_dir.glob("*.json"))
    text = public_file.read_text(encoding="utf-8")
    assert MAKER_EMAIL not in text
    assert "maker-a@redacted" in text
    assert json.loads(text)["raw_file"].startswith("raw/webhooks/")
    assert all("raw" in path.relative_to(store.root).parts for path in store.raw_dir.iterdir())


def test_duplicate_deliveries_are_each_kept(client, store):
    for _ in range(3):
        assert (
            client.post(WEBHOOK_PATH, content=EVENT_BYTES, headers=PAYPAL_HEADERS).status_code
            == 200
        )

    names = sorted(path.name for path in store.public_dir.glob("*.json"))
    assert names == [
        f"20261024T093015123456Z_{sequence:03d}_WH-FAKE-0001.json" for sequence in (1, 2, 3)
    ]


def test_invalid_json_is_still_stored(client, store):
    response = client.post(WEBHOOK_PATH, content=b"not json \xff", headers=PAYPAL_HEADERS)

    assert response.status_code == 200
    [public_file] = list(store.public_dir.glob("*no-id.json"))
    public = json.loads(public_file.read_text(encoding="utf-8"))
    assert public["event_id"] is None
    assert (store.root / public["raw_file"]).read_bytes() == b"not json \xff"


def test_storage_failure_returns_500_so_paypal_retries(client, store):
    store.raw_dir.parent.mkdir(parents=True, exist_ok=True)
    store.raw_dir.write_text("a file where a directory should be")

    response = client.post(WEBHOOK_PATH, content=EVENT_BYTES, headers=PAYPAL_HEADERS)

    assert response.status_code == 500


def test_importing_the_module_does_not_load_settings(monkeypatch):
    import importlib

    from scripts.sandbox_checks import catcher

    def fail(*_args, **_kwargs):
        raise AssertionError("settings loaded at import")

    monkeypatch.setattr("scripts.sandbox_checks.env.load_settings", fail)
    importlib.reload(catcher)
    monkeypatch.undo()
    importlib.reload(catcher)


def test_registration_body_names_every_event_type():
    body = register_webhook.registration_body("https://fake.tunnel.example/webhooks/paypal")
    names = {entry["name"] for entry in body["event_types"]}

    assert body["url"] == "https://fake.tunnel.example/webhooks/paypal"
    assert not any(name.endswith("*") for name in names)
    assert {
        "PAYMENT.PAYOUTS-ITEM.UNCLAIMED",
        "PAYMENT.PAYOUTS-ITEM.CANCELED",
        "PAYMENT.PAYOUTSBATCH.SUCCESS",
        "CUSTOMER.DISPUTE.CREATED",
        "PAYMENT.CAPTURE.COMPLETED",
    } <= names
    with pytest.raises(ValueError):
        register_webhook.registration_body("http://insecure.example/webhooks/paypal")


def stub_transport(status, payload, seen):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/oauth2/token":
            return httpx.Response(
                200, json={"access_token": "FAKE-TOKEN", "expires_in": 3600, "scope": "fake"}
            )
        seen.append(request)
        return httpx.Response(status, json=payload)

    return httpx.MockTransport(handler)


def test_register_posts_body_and_returns_only_the_id(tmp_path, settings):
    seen = []
    recorder = Recorder("D11", root=tmp_path, redactor=Redactor.from_settings(settings))
    transport = stub_transport(201, {"id": "WH-FAKE-NEW", "url": "https://fake"}, seen)

    webhook_id = asyncio.run(
        register_webhook.register(
            "https://fake.tunnel.example/webhooks/paypal", settings, recorder, transport=transport
        )
    )

    assert webhook_id == "WH-FAKE-NEW"
    [request] = seen
    assert request.method == "POST"
    assert request.url.path == "/v1/notifications/webhooks"
    assert json.loads(request.content) == register_webhook.registration_body(
        "https://fake.tunnel.example/webhooks/paypal"
    )
    recorded = "".join(path.read_text() for path in tmp_path.rglob("*register_webhook.json"))
    assert "FAKE-TOKEN" not in recorded and "FAKE-CLIENT-SECRET" not in recorded


def test_register_error_reports_paypal_error_name_only(tmp_path, settings):
    recorder = Recorder("D11", root=tmp_path, redactor=Redactor.from_settings(settings))
    transport = stub_transport(
        400, {"name": "WEBHOOK_URL_ALREADY_EXISTS", "message": "fake detail"}, []
    )

    with pytest.raises(RuntimeError, match=r"HTTP 400, WEBHOOK_URL_ALREADY_EXISTS\)") as error:
        asyncio.run(
            register_webhook.register(
                "https://fake.tunnel.example/webhooks/paypal",
                settings,
                recorder,
                transport=transport,
            )
        )
    assert "fake detail" not in str(error.value)


def test_update_patches_only_the_url_of_the_existing_webhook(tmp_path, settings):
    seen = []
    settings = dataclasses.replace(settings, webhook_id="WH-FAKE-OLD")
    recorder = Recorder("D11", root=tmp_path, redactor=Redactor.from_settings(settings))
    url = "https://new.tunnel.example/webhooks/paypal"
    transport = stub_transport(200, {"id": "WH-FAKE-OLD", "url": url}, seen)

    webhook_id = asyncio.run(
        register_webhook.update_url(url, settings, recorder, transport=transport)
    )

    assert webhook_id == "WH-FAKE-OLD"
    [request] = seen
    assert request.method == "PATCH"
    assert request.url.path == "/v1/notifications/webhooks/WH-FAKE-OLD"
    assert json.loads(request.content) == [{"op": "replace", "path": "/url", "value": url}]


def test_update_without_a_webhook_id_sends_nothing(tmp_path, settings):
    seen = []
    recorder = Recorder("D11", root=tmp_path, redactor=Redactor.from_settings(settings))

    with pytest.raises(RuntimeError, match="PAYPAL_WEBHOOK_ID"):
        asyncio.run(
            register_webhook.update_url(
                "https://new.tunnel.example/webhooks/paypal",
                settings,
                recorder,
                transport=stub_transport(200, {}, seen),
            )
        )
    assert seen == []


def test_dry_run_prints_body_without_loading_settings(monkeypatch, capsys):
    def fail(*_args, **_kwargs):
        raise AssertionError("settings loaded during dry run")

    monkeypatch.setattr(register_webhook, "load_settings", fail)

    assert (
        register_webhook.main(["--url", "https://fake.tunnel.example/webhooks/paypal", "--dry-run"])
        == 0
    )
    assert (
        json.loads(capsys.readouterr().out)["url"] == "https://fake.tunnel.example/webhooks/paypal"
    )
