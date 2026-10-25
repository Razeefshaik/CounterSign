"""Prove loss is observable over a real loopback HTTP connection."""

import os
import socket
import subprocess
import sys
import time

import httpx


def test_after_commit_connection_loss_over_tcp():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    script = (
        "from mock_paypal.app import create_app; import uvicorn; "
        "app=create_app(control_enabled=True); "
        "app.state.store.payouts.available_minor=10000; "
        f"uvicorn.run(app, host='127.0.0.1', port={port}, log_level='critical')"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", script],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    try:
        with httpx.Client(
            base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=3
        ) as client:
            for _ in range(100):
                try:
                    if client.get("/_mock/clock").status_code == 200:
                        break
                except httpx.ConnectError:
                    time.sleep(
                        0.02
                    )  # Process readiness only, not a mock business timer.
            else:
                raise AssertionError("Local test server did not start")
            token = client.post(
                "/v1/oauth2/token",
                auth=("mock", "dummy"),
                data={"grant_type": "client_credentials"},
            ).json()["access_token"]
            client.headers["Authorization"] = "Bearer " + token
            client.post(
                "/_mock/faults",
                json={
                    "match": {"method": "POST", "path": "^/v1/payments/payouts$"},
                    "action": {"type": "drop_response", "after_commit": True},
                },
            )
            payload = {
                "sender_batch_header": {"sender_batch_id": "tcp-test"},
                "items": [
                    {
                        "recipient_type": "EMAIL",
                        "receiver": "test@example.com",
                        "sender_item_id": "i1",
                        "amount": {"value": "1.00", "currency": "USD"},
                    }
                ],
            }
            import pytest

            with pytest.raises(httpx.RemoteProtocolError):
                client.post(
                    "/v1/payments/payouts",
                    json=payload,
                    headers={"PayPal-Request-Id": "tcp"},
                )
            assert len(client.get("/_mock/ground-truth").json()["payout_items"]) == 1
            replay = client.post(
                "/v1/payments/payouts",
                json=payload,
                headers={"PayPal-Request-Id": "tcp"},
            )
            assert replay.status_code == 400
            assert replay.json()["name"] == "USER_BUSINESS_ERROR"
            assert replay.json()["links"] == []
            linked = client.get(replay.json()["details"][0]["link"][0]["href"])
            assert linked.status_code == 200
            assert len(linked.json()["items"]) == 1
            assert (
                linked.json()["batch_header"]["payout_batch_id"]
                == client.get("/_mock/ground-truth").json()["payout_items"][0][
                    "payout_batch_id"
                ]
            )
            assert len(client.get("/_mock/ground-truth").json()["payout_items"]) == 1
    finally:
        process.terminate()
        process.wait(timeout=10)
