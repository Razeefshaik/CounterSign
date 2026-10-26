"""The real model switch is explicit and fails before connections when absent."""

import json
from types import SimpleNamespace

import pytest

from countersign.agent.models import ChatModelConfig, ModelError
from countersign.config import Settings
from scripts import demo_week as demo


@pytest.mark.parametrize("mode", [None, "configured"])
def test_cli_model_selection_is_explicit_and_defaults_to_scripted(monkeypatch, mode):
    observed = []

    async def run(args):
        observed.append(args.model)

    monkeypatch.setattr(demo, "run_cli", run)
    argv = ["--target", "mock"] + (["--model", mode] if mode else [])
    assert demo.main(argv) == 0
    assert observed == [mode or "scripted"]


def settings_without_io(*_):
    return Settings("postgresql://fake:fake@localhost/week_demo"), {
        key: key.lower() + "@example.test" for key in "ABC"
    }


@pytest.mark.parametrize("partial", [False, True])
def test_configured_missing_or_partial_settings_fail_before_connections(
    monkeypatch, capsys, partial
):
    monkeypatch.setattr(demo, "runtime_settings", settings_without_io)

    def configuration():
        if partial:
            raise ModelError("MODEL_API_KEY_REQUIRED")

    monkeypatch.setattr(
        ChatModelConfig, "from_environment", staticmethod(configuration), raising=False
    )

    def no_connections(*_, **__):
        pytest.fail("missing configured model must stop before database or provider connections")

    monkeypatch.setattr("countersign.db.Database", no_connections)
    monkeypatch.setattr(demo, "PayPalHTTP", no_connections)
    assert demo.main(["--target", "mock", "--model", "configured"]) == 1
    output = capsys.readouterr().out
    assert (
        "CONFIGURED_MODEL_SETTINGS_INVALID" if partial else "CONFIGURED_MODEL_SETTINGS_REQUIRED"
    ) in output


def test_scripted_mode_does_not_read_provider_settings(monkeypatch):
    def unexpected():
        pytest.fail("default scripted model must not inspect provider configuration")

    monkeypatch.setattr(
        ChatModelConfig, "from_environment", staticmethod(unexpected), raising=False
    )
    model = demo.runtime_model("scripted")
    assert isinstance(model, demo.DemoModel)
    assert demo.model_identity(model) == {"mode": "scripted", "provider": None, "model": "scripted"}


@pytest.mark.asyncio
@pytest.mark.parametrize("configured", [False, True])
async def test_resume_identity_preserves_scripted_runs_and_binds_configured_model(
    monkeypatch, configured
):
    config = ChatModelConfig("google_genai", "gemini-3-flash-preview", "FAKE-GOOGLE-KEY")
    monkeypatch.setattr(demo, "runtime_settings", settings_without_io)
    monkeypatch.setattr(
        ChatModelConfig, "from_environment", staticmethod(lambda: config), raising=False
    )
    captured = {}

    class InspectedCheckpoint(Exception):
        pass

    def checkpoint(*_, identity, redactor, **__):
        captured.update(identity)
        if configured:
            assert config.api_key not in redactor.text(config.api_key)
        raise InspectedCheckpoint

    monkeypatch.setattr(demo, "Checkpoint", checkpoint)
    args = SimpleNamespace(
        target="mock",
        env_file="unused",
        model="configured" if configured else "scripted",
        settlement="adjudicate",
        evidence_available=True,
        reason_a=None,
        poll_limit=1,
        app_url="http://127.0.0.1:8000",
        date=None,
        root="unused",
    )
    with pytest.raises(InspectedCheckpoint):
        await demo.run_cli(args)
    settings, emails = settings_without_io()
    expected = {
        "database": settings.database_url,
        "base": settings.paypal.base_url,
        "client": settings.paypal.client_id,
        "webhook": settings.paypal.webhook_id,
        "makers": emails,
        "settlement": "adjudicate",
        "reason_a": "MERCHANDISE_OR_SERVICE_NOT_RECEIVED",
        "evidence_branch": True,
        "app": "http://127.0.0.1:8000",
    }
    if configured:
        expected["model"] = {
            "mode": "configured",
            "provider": "google_genai",
            "model": "gemini-3-flash-preview",
        }
    assert captured == expected
    assert config.api_key not in json.dumps(captured)


def test_configured_mode_uses_normal_toolkit_startup_instead_of_scripted_tools(monkeypatch):
    configured = ChatModelConfig("google_genai", "gemini-3-flash-preview", "FAKE-GOOGLE-KEY")

    def scripted_tools(*_):
        pytest.fail("configured models must use the genuine isolated read toolkit")

    monkeypatch.setattr(demo, "read_registry", scripted_tools)
    assert demo.model_tools(configured, object(), object()) is None


def test_scripted_mode_retains_its_existing_injected_read_registry(monkeypatch):
    tools, http, database = object(), object(), object()

    def scripted_tools(actual_http, actual_database):
        assert actual_http is http and actual_database is database
        return tools

    monkeypatch.setattr(demo, "read_registry", scripted_tools)
    assert demo.model_tools(demo.DemoModel(), http, database) is tools
