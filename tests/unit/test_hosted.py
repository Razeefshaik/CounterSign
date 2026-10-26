"""C27 hosted demo guard and dashboard mount; invented values only, no network or .env."""

from types import SimpleNamespace

import httpx
import pytest

from countersign.config import (
    MOCK_URL,
    SANDBOX_URL,
    PayPalSettings,
    Settings,
    SettingsError,
    load_hosted_settings,
    require_hosted_demo,
)
from countersign.main import WEB_DIR, create_hosted_app

DATABASE = "postgresql://unit-user:unit-password@localhost/unit_demo"
LIVE = Settings(
    DATABASE,
    mode="live",
    owner_token="unit-owner-token",
    paypal=PayPalSettings(base_url=SANDBOX_URL, client_id="unit-id", client_secret="unit-secret"),
)


class NoNetwork:
    async def verify(self, raw, headers):
        raise AssertionError("No webhook verification in this test")

    async def get_dispute(self, resource_id):
        raise AssertionError("No PayPal reads in this test")


@pytest.fixture
def demo_environment(monkeypatch):
    for name in (
        "PAYPAL_CLIENT_ID",
        "PAYPAL_CLIENT_SECRET",
        "GOOGLE_API_KEY",
        "OWNER_TOKEN",
        "MOCK_PAYPAL_CLIENT_ID",
        "MOCK_PAYPAL_CLIENT_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DATABASE_URL", DATABASE)
    monkeypatch.setenv("COUNTERSIGN_MODE", "demo")
    monkeypatch.setenv("PAYPAL_BASE_URL", MOCK_URL)
    return monkeypatch


def test_hosted_settings_accept_demo_on_the_loopback_mock(demo_environment):
    settings = load_hosted_settings()
    assert settings.mode == "demo" and settings.paypal.is_mock
    assert settings.paypal.client_id == "mock-client"


@pytest.mark.parametrize("name", ["PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET", "GOOGLE_API_KEY"])
def test_hosted_settings_refuse_credentials_by_name_only(demo_environment, name):
    demo_environment.setenv(name, "unit-private-value")
    with pytest.raises(SettingsError) as error:
        load_hosted_settings()
    assert name in str(error.value)
    assert "unit-private-value" not in str(error.value)


@pytest.mark.parametrize(
    ("mode", "url"),
    [("live", SANDBOX_URL), ("live", MOCK_URL), ("demo", SANDBOX_URL), ("staging", MOCK_URL)],
)
def test_hosted_settings_never_allow_live_or_sandbox(demo_environment, mode, url):
    demo_environment.setenv("COUNTERSIGN_MODE", mode)
    demo_environment.setenv("PAYPAL_BASE_URL", url)
    demo_environment.setenv("OWNER_TOKEN", "unit-owner-token")
    with pytest.raises(SettingsError):
        load_hosted_settings()


def test_require_hosted_demo_refuses_live_settings():
    with pytest.raises(SettingsError, match="demo mode on the local mock only"):
        require_hosted_demo(LIVE)


def test_hosted_factory_refuses_injected_live_settings():
    with pytest.raises(SettingsError):
        create_hosted_app(settings=LIVE)


def test_hosted_factory_reads_settings_when_called(demo_environment):
    demo_environment.setenv("PAYPAL_CLIENT_SECRET", "unit-private-value")
    with pytest.raises(SettingsError):
        create_hosted_app()


@pytest.mark.asyncio
async def test_hosted_app_serves_dashboard_and_api_without_workers():
    assert (WEB_DIR / "index.html").is_file()
    fake = NoNetwork()
    app = create_hosted_app(
        settings=Settings(DATABASE),
        database=SimpleNamespace(),
        http=fake,
        reader=fake,
        verifier=fake,
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        assert not hasattr(app.state, "executor")
        page = await client.get("/")
        assert page.status_code == 200 and "text/html" in page.headers["content-type"]
        assert (await client.get("/app.mjs")).status_code == 200
        meta = (await client.get("/api/v1/meta")).json()
        assert meta["mode"] == "demo" and meta["paypal_env"] == "mock"
        assert meta["simulation_label"] == "Simulated: mock PayPal"
        missing = await client.get("/absent")
        assert missing.status_code == 404
        assert missing.json() == {"error": {"code": "NOT_FOUND", "message": "Resource not found"}}
    # Lifespan exit restores the original routes, including the static mount.
    assert not any(getattr(route, "name", None) == "web" for route in app.router.routes)
