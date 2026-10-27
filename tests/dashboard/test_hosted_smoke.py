"""Public HTTP smoke contract; invented API responses, no database or network."""

from copy import deepcopy

import httpx
import pytest

from scripts.hosted_smoke import HostedSmoke, SmokeError, main


def transport(*, mismatch=None):
    state = {"step": 0, "waiting": False}
    money = lambda minor: {"minor": minor, "currency": "USD"}
    line = {
        "id": "line",
        "state": "PAID",
        "amount": money(108600),
        "attempts": [{"state": "SUCCESS", "amount": money(108600), "paypal_item_id": "item"}],
    }

    def handle(request):
        path = request.url.path
        if path == "/":
            return httpx.Response(
                200, text='<div id="simulation"></div>', headers={"content-type": "text/html"}
            )
        if path.endswith("/meta"):
            doc = {
                "mode": "demo",
                "paypal_env": "mock",
                "simulated": True,
                "simulation_label": "Simulated: mock PayPal",
            }
            if mismatch == "live":
                doc["mode"] = "live"
        elif path.endswith("/demo/state"):
            running = state["waiting"]
            state["waiting"] = False
            doc = {
                "step": state["step"],
                "total": 11,
                "complete": state["step"] == 11,
                "running": running,
                "steps": [{"done": True}] * 11,
            }
        elif path.endswith("/demo/reset"):
            state["step"] = 0
            return httpx.Response(202, json={})
        elif path.endswith("/demo/step"):
            state["step"] += 1
            state["waiting"] = True
            return httpx.Response(202, json={})
        elif path.endswith("/payout-runs"):
            doc = {
                "items": [{"id": "friday", "state": "DONE", "total": money(108600)}],
                "next_cursor": None,
            }
        elif path.endswith("/payout-runs/friday"):
            doc = {"lines": [deepcopy(line)], "replacements": [], "approval": {"used_at": "now"}}
            if mismatch == "duplicate":
                doc["lines"][0]["attempts"] *= 2
        elif path.endswith("/cash"):
            doc = {"paypal": {"available": money(0 if mismatch == "cash" else 35400)}}
        elif path.endswith("/makers"):
            doc = {"items": [{"id": "B", "display_name": "Maker B"}], "next_cursor": None}
        elif path.endswith("/disputes"):
            doc = {"items": [{"id": "dispute", "order": {"maker_id": "B"}}], "next_cursor": None}
        elif path.endswith("/reservations"):
            doc = {
                "items": [
                    {
                        "kind": "DISPUTE_EARMARK",
                        "state": "ACTIVE",
                        "dispute_id": "dispute",
                        "amount": money(5400),
                        "affects_cash": False,
                    }
                ],
                "next_cursor": None,
            }
        elif path.endswith("/playground/attacks"):
            doc = {
                "items": [
                    {"id": key}
                    for key in [
                        "refund_to_other_account",
                        "retry_settled_payout",
                        "refund_without_owner",
                        "custom",
                    ]
                ]
            }
        elif path.endswith("/playground/attempts"):
            doc = {"verdict": "BLOCK", "written": mismatch == "write"}
        else:
            raise AssertionError("Unexpected endpoint")
        return httpx.Response(200, json=doc)

    return httpx.MockTransport(handle)


def test_smoke_waits_for_each_command_and_checks_week_and_playground():
    with httpx.Client(base_url="http://localhost:8080", transport=transport()) as client:
        HostedSmoke(client, pause=lambda seconds: None).run()


@pytest.mark.parametrize("mismatch", ["live", "duplicate", "cash", "write"])
def test_smoke_refuses_mismatches(mismatch):
    with (
        httpx.Client(
            base_url="http://localhost:8080", transport=transport(mismatch=mismatch)
        ) as client,
        pytest.raises(SmokeError),
    ):
        HostedSmoke(client, pause=lambda seconds: None).run()


def test_smoke_timeout_is_bounded():
    def handle(request):
        return httpx.Response(200, json={"running": True})

    times = iter([0, 0, 2])
    with (
        httpx.Client(base_url="http://localhost:8080", transport=httpx.MockTransport(handle)) as client,
        pytest.raises(SmokeError, match="Timed out"),
    ):
        HostedSmoke(client, timeout=1, clock=lambda: next(times), pause=lambda _: None).idle()


def test_cli_does_not_echo_transport_credentials(monkeypatch, capsys):
    def fail(*args, **kwargs):
        raise httpx.ConnectError("private-value")

    monkeypatch.setattr(httpx.Client, "request", fail)
    assert main(["http://localhost:8080"]) == 1
    assert "private-value" not in capsys.readouterr().err
