"""App lifecycle wiring is tested without external traffic or private environment."""

import asyncio
import subprocess
import sys
from types import SimpleNamespace

import httpx
import pytest

from countersign.config import SANDBOX_URL, PayPalSettings, Settings
from countersign.db import Database
from countersign.main import create_app
from countersign.paypal.webhook_verify import Verification

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


class NoNetwork:
    async def verify(self, raw, headers):
        return Verification.UNKNOWN

    async def get_dispute(self, resource_id):
        raise AssertionError("Disabled worker must not GET")

    async def aclose(self):
        raise AssertionError("Caller-owned transport must remain open")


async def test_main_mounts_routes_and_preserves_injected_database(db_schema):
    settings = Settings(db_schema.url)
    fake = NoNetwork()
    async with Database(db_schema.conninfo) as database:
        app = create_app(
            settings=settings,
            database=database,
            http=fake,
            reader=fake,
            verifier=fake,
            start_worker=False,
        )
        # Restarting a lifespan must not retain routes bound to old owned resources.
        for _ in range(2):
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as client:
                    meta = await client.get("/api/v1/meta")
                    assert meta.json()["simulation_label"] == "Simulated: mock PayPal"
                    assert meta.headers["X-CounterSign-Dashboard-Source"] == "fixtures"
                    assert app.state.dashboard_source == "fixtures"
                    assert (await client.get("/api/v1/incidents")).status_code == 200
                    assert (
                        await client.post(
                            "/webhooks/paypal",
                            json={
                                "id": "WH-MAIN",
                                "event_type": "CUSTOMER.DISPUTE.CREATED",
                                "resource": {"dispute_id": "PP-D-MAIN"},
                            },
                        )
                    ).status_code == 200
                    # Both owner routes exist; malformed UUID hits validation, not 404.
                    assert (
                        await client.post(
                            "/api/v1/makers/bad/payout-email",
                            json={"payout_email": "maker@example.test", "confirm": True},
                        )
                    ).status_code == 422
                    assert (await client.post("/api/v1/payout-runs", json={})).status_code == 422
                assert not database.pool.closed
            assert not database.pool.closed


async def test_live_metadata_and_auth_come_from_settings():
    settings = Settings(
        "postgresql://unused/db",
        mode="live",
        owner_token="owner-test-token",
        paypal=PayPalSettings(
            base_url=SANDBOX_URL, client_id="test-id", client_secret="test-secret"
        ),
    )
    fake = NoNetwork()
    app = create_app(
        settings=settings,
        database=SimpleNamespace(),
        http=fake,
        reader=fake,
        verifier=fake,
        start_worker=False,
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        response = await client.get("/api/v1/meta")
        assert response.headers["X-CounterSign-Dashboard-Source"] == "fixtures"
        meta = response.json()
        assert meta["mode"] == "live" and meta["paypal_env"] == "sandbox"
        assert not meta["simulated"] and meta["simulation_label"] is None
        response = await client.post(
            "/api/v1/makers/0192f1c4-7a10-7c3e-9b1a-5d2f00000301/payout-email",
            json={"payout_email": "maker@example.test", "confirm": True},
        )
        assert response.status_code == 401
        assert "owner-test-token" not in response.text
        assert set(response.json()["error"]) == {"code", "message"}
        missing = await client.get("/absent")
        assert missing.status_code == 404
        assert missing.json() == {"error": {"code": "NOT_FOUND", "message": "Resource not found"}}


async def test_settings_read_at_startup_only(monkeypatch):
    def failing_settings():
        raise ValueError("Runtime settings requested")

    monkeypatch.setattr("countersign.main.load_settings", failing_settings)
    app = create_app(start_worker=False)
    with pytest.raises(RuntimeError, match="application lifecycle failed") as failure:
        async with app.router.lifespan_context(app):
            pytest.fail("Missing settings must not enter app lifespan")
    assert failure.value.__suppress_context__


async def test_cli_launcher_uses_selector_loop_without_opening_server():
    code = """
import asyncio
import uvicorn
from countersign.main import main
class Server:
    def __init__(self, config):
        pass
    async def serve(self):
        print(isinstance(asyncio.get_running_loop(), asyncio.SelectorEventLoop))
uvicorn.Server = Server
main()
"""
    result = await asyncio.to_thread(
        subprocess.run, [sys.executable, "-c", code], capture_output=True, check=True, text=True
    )
    assert result.stdout.strip() == "True"
    assert not result.stderr


async def test_owned_database_closed_and_shutdown_failures_masked(monkeypatch):
    states = []

    class OwnedDatabase:
        def __init__(self, settings):
            pass

        async def __aenter__(self):
            states.append("opened")
            return self

        async def __aexit__(self, *args):
            states.append("closed")
            raise RuntimeError("private-driver-credential")

    monkeypatch.setattr("countersign.main.Database", OwnedDatabase)
    fake = NoNetwork()
    app = create_app(
        settings=Settings("postgresql://unused/db"),
        http=fake,
        reader=fake,
        verifier=fake,
        start_worker=False,
    )
    with pytest.raises(RuntimeError, match="application lifecycle failed") as failure:
        async with app.router.lifespan_context(app):
            assert states == ["opened"]
    assert states == ["opened", "closed"]
    assert "private-driver-credential" not in str(failure.value)
    assert failure.value.__suppress_context__
