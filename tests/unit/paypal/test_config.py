"""Configuration tests use invented values; they never open .env."""

from dataclasses import replace

import pytest

from countersign.config import (
    MOCK_URL,
    SANDBOX_URL,
    PayPalSettings,
    Settings,
    SettingsError,
    load_settings,
)

DATABASE = "postgresql://unit-user:unit-password@localhost/unit_test"


@pytest.mark.parametrize(
    "url",
    [
        SANDBOX_URL,
        SANDBOX_URL + "/",
        SANDBOX_URL + ":443",
        MOCK_URL,
        "http://localhost:1234",
        "https://localhost:8443/",
    ],
)
def test_approved_origins(url):
    assert PayPalSettings(base_url=url).base_url.rstrip("/") == url.rstrip("/")


@pytest.mark.parametrize(
    "url",
    [
        "https://api-m.paypal.com",
        "https://api.paypal.com",
        "http://api-m.sandbox.paypal.com",
        "https://api-m.sandbox.paypal.com.attacker.invalid",
        "https://example.invalid",
        "http://localhost.attacker.invalid",
        "http://127.0.0.2",
        "http://[::1]",
        "http://private:password@localhost",
        "http://localhost/path",
        "http://localhost?token=private",
        "http://localhost#private",
        "http://localhost:0",
        "http://localhost:65536",
        "https://api-m.sandbox.paypal.com:1234",
    ],
)
def test_other_origins_rejected_without_echoing(url):
    with pytest.raises(SettingsError) as error:
        PayPalSettings(base_url=url)
    assert url not in str(error.value)
    assert "private" not in str(error.value)


def test_all_settings_representations_mask_values():
    paypal = PayPalSettings(
        client_id="unit-client", client_secret="unit-secret", webhook_id="unit-hook"
    )
    settings = Settings(DATABASE, paypal=paypal, owner_token="unit-owner-token")
    text = repr(settings) + repr(paypal)
    for value in (
        DATABASE,
        MOCK_URL,
        "unit-client",
        "unit-secret",
        "unit-hook",
        "unit-owner-token",
    ):
        assert value not in text


def test_modes_fail_closed():
    with pytest.raises(SettingsError):
        Settings(DATABASE, mode="live")
    with pytest.raises(SettingsError):
        Settings(DATABASE, paypal=PayPalSettings(base_url=SANDBOX_URL))
    with pytest.raises(SettingsError):
        Settings(DATABASE, mode="invalid")
    valid = Settings(
        DATABASE,
        mode="live",
        paypal=PayPalSettings(
            base_url=SANDBOX_URL, client_id="unit-client", client_secret="unit-secret"
        ),
        owner_token="unit-owner-token",
    )
    with pytest.raises(SettingsError):
        replace(valid, owner_token="")


def test_runtime_demo_uses_only_mock_credentials(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", DATABASE)
    monkeypatch.setenv("COUNTERSIGN_MODE", "demo")
    for name in ("PAYPAL_BASE_URL", "MOCK_PAYPAL_CLIENT_ID", "MOCK_PAYPAL_CLIENT_SECRET"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PAYPAL_CLIENT_ID", "sandbox-client-should-not-be-used")
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "sandbox-secret-should-not-be-used")
    settings = load_settings()
    assert settings.paypal.is_mock
    assert settings.paypal.client_id == "mock-client"
    assert settings.paypal.client_secret == "mock-secret"


def test_runtime_live_requires_owner_token(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", DATABASE)
    monkeypatch.setenv("COUNTERSIGN_MODE", "live")
    monkeypatch.setenv("PAYPAL_CLIENT_ID", "unit-client")
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "unit-secret")
    monkeypatch.delenv("PAYPAL_BASE_URL", raising=False)
    monkeypatch.delenv("OWNER_TOKEN", raising=False)
    with pytest.raises(SettingsError, match="OWNER_TOKEN"):
        load_settings()
    monkeypatch.setenv("OWNER_TOKEN", "unit-owner-token")
    assert load_settings().paypal.base_url == SANDBOX_URL
