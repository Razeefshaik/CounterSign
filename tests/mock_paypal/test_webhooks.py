import base64
import json
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient
from test_payouts import advance, authenticated, create, item

from mock_paypal.app import create_app
from mock_paypal.webhooks import signed_message


@pytest.fixture
def setup():
    delivered = []

    async def delivery(url, headers, raw):
        delivered.append((url, dict(headers), raw))
        return 200

    app = create_app(control_enabled=True, webhook_delivery=delivery)
    app.state.store.payouts.available_minor = 150000
    with TestClient(app) as client:
        authenticated(client)
        reg = client.post(
            "/v1/notifications/webhooks",
            json={
                "url": "http://localhost:8100/webhooks/paypal",
                "event_types": [{"name": "*"}],
            },
        )
        assert reg.status_code == 201
        yield client, reg.json()["id"], delivered


def verification(client, id, headers, raw):
    return client.post(
        "/v1/notifications/verify-webhook-signature",
        json={
            "auth_algo": headers.get("paypal-auth-algo"),
            "cert_url": headers.get("paypal-cert-url"),
            "transmission_id": headers.get("paypal-transmission-id"),
            "transmission_time": headers.get("paypal-transmission-time"),
            "transmission_sig": headers.get("paypal-transmission-sig"),
            "webhook_id": id,
            "webhook_event": json.loads(raw),
        },
    ).json()["verification_status"]


def test_signature_verified_independently_with_certificate(setup, tmp_path):
    client, id, delivered = setup
    create(client)
    advance(client, 34)
    assert delivered == []
    advance(client, 1)
    assert len(delivered) == 3
    _, headers, raw = delivered[1]
    assert verification(client, id, headers, raw) == "SUCCESS"
    openssl = shutil.which("openssl")
    if not openssl:
        pytest.skip("OpenSSL unavailable for independent certificate verification")
    cert = tmp_path / "cert.pem"
    cert.write_bytes(client.get("/_mock/cert.pem").content)
    pub, signature, data = (tmp_path / name for name in ["pub.pem", "signature.bin", "data.bin"])
    public = subprocess.check_output([openssl, "x509", "-in", str(cert), "-pubkey", "-noout"])
    pub.write_bytes(public)
    signature.write_bytes(base64.b64decode(headers["paypal-transmission-sig"]))
    data.write_bytes(
        signed_message(
            headers["paypal-transmission-id"],
            headers["paypal-transmission-time"],
            id,
            raw,
        )
    )
    result = subprocess.run(
        [
            openssl,
            "dgst",
            "-sha256",
            "-verify",
            str(pub),
            "-signature",
            str(signature),
            str(data),
        ],
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0
    changed = json.loads(raw)
    changed["resource"]["transaction_status"] = "SUCCESS"
    assert verification(client, id, headers, json.dumps(changed).encode()) == "FAILURE"
    assert verification(client, "WH-WRONG", headers, raw) == "FAILURE"


@pytest.mark.parametrize("mode", ["invalid", "missing", "wrong_webhook_id"])
def test_forged_signatures_rejected(setup, mode):
    client, id, delivered = setup
    client.post(
        "/_mock/webhooks/forge",
        json={
            "event_type": "CUSTOMER.DISPUTE.RESOLVED",
            "resource": {},
            "signature": mode,
        },
    )
    advance(client, 5)
    assert len(delivered) == 1
    assert verification(client, id, delivered[0][1], delivered[0][2]) == "FAILURE"


def test_webhook_list_resend_and_replay(setup):
    client, id, delivered = setup
    create(client)
    advance(client, 35)
    assert len(client.get("/v1/notifications/webhooks").json()["webhooks"]) == 1
    events = client.get("/v1/notifications/webhooks-events").json()["events"]
    assert len(events) == 3
    first = events[0]["id"]
    assert client.post("/v1/notifications/webhooks-events/" + first + "/resend").status_code == 202
    advance(client, 5)
    assert len(delivered) == 4
    assert client.post("/_mock/webhooks/forge", json={"replay_of": first}).status_code == 202
    advance(client, 5)
    assert verification(client, id, delivered[-1][1], delivered[-1][2]) == "SUCCESS"


@pytest.mark.parametrize(
    "action, expected",
    [
        ({"type": "drop"}, 0),
        ({"type": "duplicate", "copies": 2}, 2),
        ({"type": "delay", "seconds": 100}, 0),
    ],
)
def test_outgoing_faults(setup, action, expected):
    client, _, delivered = setup
    client.post(
        "/_mock/webhook-faults",
        json={
            "match": {"event_type": "PAYMENT.PAYOUTS-ITEM.UNCLAIMED", "nth": 1},
            "action": action,
        },
    )
    create(client)
    advance(client, 35)
    matching = lambda: [
        json.loads(raw)
        for _, _, raw in delivered
        if json.loads(raw)["event_type"].endswith(".UNCLAIMED")
    ]
    assert len(matching()) == expected
    if action["type"] == "delay":
        advance(client, 100)
        assert len(matching()) == 1


def test_cancel_default_emits_only_returned(setup):
    client, _, delivered = setup
    batch = create(client)
    advance(client, 35)
    payout = item(client, batch)
    delivered.clear()
    response = client.post("/v1/payments/payouts-item/" + payout["payout_item_id"] + "/cancel")
    assert response.status_code == 200
    assert response.json()["transaction_status"] == "RETURNED"
    advance(client, 5)
    assert [json.loads(raw)["event_type"] for _, _, raw in delivered] == [
        "PAYMENT.PAYOUTS-ITEM.RETURNED"
    ]


def test_cancel_order_reordering_and_reset(setup):
    client, _, delivered = setup
    client.post("/_mock/config", json={"cancel_emits": ["CANCELED", "RETURNED"]})
    batch = create(client)
    advance(client, 35)
    id = item(client, batch)["payout_item_id"]
    delivered.clear()
    client.post(
        "/_mock/webhook-faults",
        json={
            "match": {"event_type": "PAYMENT.PAYOUTS-ITEM.CANCELED"},
            "action": {
                "type": "reorder_after",
                "event_type": "PAYMENT.PAYOUTS-ITEM.RETURNED",
            },
        },
    )
    client.post("/v1/payments/payouts-item/" + id + "/cancel")
    advance(client, 5)
    assert [json.loads(raw)["event_type"].rsplit(".", 1)[-1] for _, _, raw in delivered] == [
        "RETURNED",
        "CANCELED",
    ]
    client.post("/_mock/reset", json={"seed": 17})
    assert client.app.state.store.webhooks.events == {}
    assert client.app.state.store.webhooks.registrations == {}
    assert client.app.state.store.emit.__self__ is client.app.state.webhook_service


def test_local_callbacks_only_and_type_filter(setup):
    client, _, _delivered = setup
    assert (
        client.post(
            "/v1/notifications/webhooks",
            json={
                "url": "https://api-m.paypal.com/anything",
                "event_types": [{"name": "*"}],
            },
        ).status_code
        == 422
    )
    assert client.post("/v1/notifications/webhooks-events/missing/resend").status_code == 404
    assert (
        client.post(
            "/_mock/webhook-faults",
            json={
                "match": {"event_type": "*"},
                "action": {"type": "duplicate", "copies": 0},
            },
        ).status_code
        == 422
    )
