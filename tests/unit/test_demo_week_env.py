"""Generated fake config only: mock rehearsals cannot import sandbox settings."""

import json

import pytest

from scripts import demo_week as demo

MODEL_VALUES = {
    "COUNTERSIGN_LLM_PROVIDER": "google_genai",
    "COUNTERSIGN_LLM_MODEL": "gemini-3.5-flash",
    "GOOGLE_API_KEY": "FAKE-MODEL-KEY",
}
IGNORED_VALUES = {
    "PAYPAL_CLIENT_ID": "FAKE-SANDBOX-CLIENT",
    "PAYPAL_CLIENT_SECRET": "FAKE-SANDBOX-SECRET",
    "PAYPAL_BASE_URL": "https://api-m.sandbox.paypal.com",
    "PAYPAL_WEBHOOK_ID": "FAKE-SANDBOX-WEBHOOK",
    "DATABASE_URL": "postgresql://fake:fake@localhost/working",
    "OWNER_TOKEN": "FAKE-OWNER-TOKEN",
    "COUNTERSIGN_MODE": "live",
    "MOCK_PAYPAL_CLIENT_ID": "FAKE-REPLACEMENT-CLIENT",
    "MOCK_PAYPAL_CLIENT_SECRET": "FAKE-REPLACEMENT-SECRET",
    "COUNTERSIGN_TOOLKIT_PYTHON": "FAKE-UNAPPROVED-INTERPRETER",
}


def fake_file(tmp_path, values):
    path = tmp_path / "generated-model-settings.txt"
    path.write_text(
        "\n".join(f"{name}={value}" for name, value in values.items()), encoding="utf-8"
    )
    return str(path)


def test_loader_copies_only_the_three_model_names(tmp_path, monkeypatch, capsys, caplog):
    source = {}
    monkeypatch.setattr(demo.os, "environ", source)
    path = fake_file(tmp_path, {**MODEL_VALUES, **IGNORED_VALUES})
    demo.load_mock_model_environment(path)
    assert source == MODEL_VALUES
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""
    assert "FAKE-" not in caplog.text


@pytest.mark.parametrize("existing", ["process-value", ""])
def test_process_values_always_win_even_when_explicitly_empty(tmp_path, monkeypatch, existing):
    source = {name: existing for name in MODEL_VALUES}
    source.update(IGNORED_VALUES)
    original = dict(source)
    monkeypatch.setattr(demo.os, "environ", source)
    path = fake_file(tmp_path, {**MODEL_VALUES, **IGNORED_VALUES})
    demo.load_mock_model_environment(path)
    assert source == original


def test_interpolation_cannot_copy_a_paypal_secret_into_a_model_value(tmp_path, monkeypatch):
    source = {"PAYPAL_CLIENT_SECRET": "FAKE-PROCESS-SECRET"}
    monkeypatch.setattr(demo.os, "environ", source)
    path = fake_file(
        tmp_path,
        {
            **MODEL_VALUES,
            "PAYPAL_CLIENT_SECRET": "FAKE-FILE-SECRET",
            "GOOGLE_API_KEY": "${PAYPAL_CLIENT_SECRET}",
        },
    )
    demo.load_mock_model_environment(path)
    assert source["GOOGLE_API_KEY"] == "${PAYPAL_CLIENT_SECRET}"
    assert source["PAYPAL_CLIENT_SECRET"] == "FAKE-PROCESS-SECRET"
    assert all(value != "FAKE-FILE-SECRET" for value in source.values())


def test_configured_mock_cli_uses_fake_file_but_keeps_process_mock_settings(
    tmp_path, monkeypatch, capsys
):
    source = {
        "DATABASE_URL": "postgresql://fake:fake@localhost/week_demo",
        "COUNTERSIGN_MODE": "demo",
        "COUNTERSIGN_TOOLKIT_PYTHON": "trusted-process-interpreter",
    }
    monkeypatch.setattr(demo.os, "environ", source)
    path = fake_file(tmp_path, {**MODEL_VALUES, **IGNORED_VALUES})
    captured = {}

    def checkpoint(*_, identity, **__):
        captured.update(identity)
        raise demo.SliceError("TEST_BEFORE_CONNECTIONS")

    monkeypatch.setattr(demo, "Checkpoint", checkpoint)
    assert demo.main(["--target", "mock", "--model", "configured", "--env-file", path]) == 1
    assert captured["model"] == {
        "mode": "configured",
        "provider": "google_genai",
        "model": "gemini-3.5-flash",
    }
    assert captured["base"] == "http://127.0.0.1:8099"
    assert captured["database"] == "postgresql://fake:fake@localhost/week_demo"
    assert source["COUNTERSIGN_MODE"] == "demo"
    assert source["COUNTERSIGN_TOOLKIT_PYTHON"] == "trusted-process-interpreter"
    assert "PAYPAL_CLIENT_SECRET" not in source
    assert capsys.readouterr().out.strip() == "TEST_BEFORE_CONNECTIONS"
    assert "FAKE-MODEL-KEY" not in json.dumps(captured)


def test_partial_file_configuration_fails_clearly_before_connections(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        demo.os,
        "environ",
        {
            "DATABASE_URL": "postgresql://fake:fake@localhost/week_demo",
        },
    )
    path = fake_file(tmp_path, {"COUNTERSIGN_LLM_PROVIDER": "google_genai"})
    monkeypatch.setattr(
        demo, "Checkpoint", lambda *_args, **_kw: pytest.fail("no client before config")
    )
    assert demo.main(["--target", "mock", "--model", "configured", "--env-file", path]) == 1
    assert capsys.readouterr().out.strip() == "CONFIGURED_MODEL_SETTINGS_INVALID"


def test_default_scripted_mock_never_opens_the_config_file(monkeypatch, capsys):
    monkeypatch.setattr(
        demo.os,
        "environ",
        {
            "DATABASE_URL": "postgresql://fake:fake@localhost/week_demo",
        },
    )

    def no_file(*_args, **_kwargs):
        pytest.fail("scripted mock mode must not load any owner environment file")

    def checkpoint(*_args, **_kwargs):
        raise demo.SliceError("TEST_SCRIPTED_NO_FILE")

    monkeypatch.setattr(demo, "load_mock_model_environment", no_file)
    monkeypatch.setattr("dotenv.dotenv_values", no_file)
    monkeypatch.setattr("dotenv.load_dotenv", no_file)
    monkeypatch.setattr(demo, "Checkpoint", checkpoint)
    assert demo.main(["--target", "mock", "--env-file", "never-open-this-file"]) == 1
    assert capsys.readouterr().out.strip() == "TEST_SCRIPTED_NO_FILE"


def test_malformed_generated_file_never_prints_its_contents(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        demo.os,
        "environ",
        {
            "DATABASE_URL": "postgresql://fake:fake@localhost/week_demo",
        },
    )
    path = tmp_path / "invalid-generated-config.txt"
    path.write_bytes(b"GOOGLE_API_KEY=FAKE-PRIVATE-KEY\xff")
    assert demo.main(["--target", "mock", "--model", "configured", "--env-file", str(path)]) == 1
    captured = capsys.readouterr()
    assert captured.out.strip() == "DEMO_STOPPED_REVIEW_CHECKPOINT"
    assert "FAKE-PRIVATE-KEY" not in captured.out + captured.err
