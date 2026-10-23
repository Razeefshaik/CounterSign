import json
from urllib.parse import quote

import pytest

from scripts.sandbox_checks.env import SandboxSettings, SettingsError, load_settings
from scripts.sandbox_checks.redact import REDACTED, Redactor


@pytest.fixture
def settings():
    return SandboxSettings(
        client_id="FAKE-CLIENT-12345",
        client_secret="FAKE-SECRET/12345+",
        buyer_email="buyer@fake.example",
        maker_a_email="maker-a@fake.example",
        maker_b_email="maker-b@fake.example",
        maker_c_email="maker-c@fake.example",
        unregistered_emails=("unreg-1@fake.example", "unreg-2@fake.example"),
        database_url="postgresql://fake-user:fake-password@localhost/countersign_test",
    )


def test_redacts_nested_secrets_headers_and_emails_without_mutating(settings):
    original = {
        "request_headers": {"Authorization": "Basic FAKE-BASIC", "paypal-debug-id": "debug-123"},
        "request_body": {"client_id": settings.client_id, "client_secret": settings.client_secret},
        "response_body": {
            "access_token": "FAKE-ACCESS-TOKEN",
            "scope": "payouts disputes reporting",
            "items": [{"receiver": email} for email in settings.email_aliases],
            "message": f"Token FAKE-ACCESS-TOKEN; secret {settings.client_secret}",
            "unknown_email": "stranger@fake.example",
        },
        "database": settings.database_url,
    }

    result = Redactor.from_settings(settings).redact(original)
    serialized = json.dumps(result)

    for private in (
        settings.client_id,
        settings.client_secret,
        settings.database_url,
        "FAKE-ACCESS-TOKEN",
        "stranger@fake.example",
        *settings.email_aliases,
    ):
        assert private not in serialized
    assert "Authorization" not in result["request_headers"]
    assert result["request_headers"]["paypal-debug-id"] == "debug-123"
    assert result["response_body"]["scope"] == "payouts disputes reporting"
    assert result["response_body"]["items"][1]["receiver"] == "maker-a@redacted"
    assert original["response_body"]["access_token"] == "FAKE-ACCESS-TOKEN"


def test_encoded_values_mixed_case_emails_and_bearer_strings_are_redacted(settings):
    redactor = Redactor.from_settings(settings)
    value = (
        f"/path?secret={quote(settings.client_secret, safe='')}&"
        "recipient=MAKER-A%40FAKE.EXAMPLE Bearer FAKE-UNKNOWN-TOKEN"
    )

    result = redactor.text(value)

    assert settings.client_secret not in result
    assert quote(settings.client_secret, safe="") not in result
    assert "maker-a@redacted" in result
    assert "FAKE-UNKNOWN-TOKEN" not in result


def test_byte_bodies_and_case_insensitive_sensitive_keys(settings):
    result = Redactor.from_settings(settings).redact(
        {
            "AUTHORIZATION": "Bearer FAKE-TOKEN",
            "Proxy-Authorization": "Basic FAKE-BASIC",
            "Set-Cookie": "FAKE-COOKIE",
            "Client-Secret": "FAKE-OTHER-SECRET",
            "body": b"maker-a@fake.example",
        }
    )

    assert result == {"Set-Cookie": REDACTED, "Client-Secret": REDACTED, "body": "maker-a@redacted"}
    assert Redactor().redact(b"\xff\xfe") == "[binary body omitted]"


def test_repr_does_not_expose_any_configuration_value(settings):
    displayed = repr(settings) + repr(Redactor.from_settings(settings))
    for private in (settings.client_id, settings.client_secret, settings.database_url):
        assert private not in displayed
    for email in settings.email_aliases:
        assert email not in displayed


def test_load_settings_uses_only_fake_environment_file(tmp_path, monkeypatch):
    monkeypatch.setattr("scripts.sandbox_checks.env.os.environ", {})
    fake_env = tmp_path / "fake.env"
    fake_env.write_text(
        "PAYPAL_ENV=sandbox\nPAYPAL_CLIENT_ID=FAKE-ID\nPAYPAL_CLIENT_SECRET=FAKE-SECRET\n"
        "SBX_UNREG_EMAILS= first@fake.example, second@fake.example \n",
        encoding="utf-8",
    )

    loaded = load_settings(fake_env)

    assert loaded.client_id == "FAKE-ID"
    assert loaded.unregistered_emails == ("first@fake.example", "second@fake.example")
    assert "FAKE-SECRET" not in repr(loaded)


@pytest.mark.parametrize("environment", ("live", "https://untrusted.example", "FAKE-PRIVATE-VALUE"))
def test_settings_refuse_non_sandbox_environment_without_echoing_it(environment):
    with pytest.raises(SettingsError) as error:
        SandboxSettings(client_id="FAKE-ID", client_secret="FAKE-SECRET", paypal_env=environment)
    assert environment not in str(error.value)


def test_missing_settings_errors_contain_only_names():
    with pytest.raises(SettingsError, match="PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET"):
        SandboxSettings(client_id="", client_secret="")
