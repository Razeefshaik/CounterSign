"""Only explicit fake mappings are read; no provider, file or network is used."""

from types import SimpleNamespace

import pytest

from countersign.agent import runtime
from countersign.agent.models import ChatModelConfig, ModelError

SETTINGS = {
    "COUNTERSIGN_LLM_PROVIDER": "google_genai",
    "COUNTERSIGN_LLM_MODEL": "gemini-3.5-flash",
    "GOOGLE_API_KEY": "FAKE-SECRET-KEY",
}


@pytest.mark.parametrize("settings", [{}, {name: " " for name in SETTINGS}])
def test_an_unset_configuration_stays_disabled(settings):
    assert runtime.load_chat_model_config(settings) is None


def test_runtime_environment_is_read_only_when_requested(monkeypatch):
    source = {}
    monkeypatch.setattr(runtime, "os", SimpleNamespace(environ=source))
    assert runtime.load_chat_model_config() is None
    source.update(SETTINGS)
    config = runtime.load_chat_model_config()
    assert config.provider == "google_genai"
    assert config.model == "gemini-3.5-flash"
    assert config.api_key == "FAKE-SECRET-KEY"
    assert "FAKE-SECRET-KEY" not in repr(config)


def test_the_existing_facade_config_type_exposes_the_runtime_loader():
    config = ChatModelConfig.from_environment(SETTINGS)
    assert isinstance(config, ChatModelConfig)
    assert config.provider == "google_genai" and config.model == "gemini-3.5-flash"
    assert config.api_key == "FAKE-SECRET-KEY"
    assert ChatModelConfig.from_environment({}) is None


@pytest.mark.parametrize(
    "missing,code",
    [
        ("COUNTERSIGN_LLM_PROVIDER", "MODEL_PROVIDER_AND_ID_REQUIRED"),
        ("GOOGLE_API_KEY", "MODEL_API_KEY_REQUIRED"),
    ],
)
def test_partial_configuration_fails_without_exposing_the_key(missing, code, caplog):
    settings = {name: value for name, value in SETTINGS.items() if name != missing}
    with pytest.raises(ModelError, match=code) as error:
        runtime.load_chat_model_config(settings)
    assert "FAKE-SECRET-KEY" not in str(error.value)
    assert "FAKE-SECRET-KEY" not in caplog.text


@pytest.mark.parametrize("name", ["COUNTERSIGN_LLM_PROVIDER", "COUNTERSIGN_LLM_MODEL"])
def test_labels_cannot_be_used_to_print_the_key(name, caplog):
    settings = {**SETTINGS, name: "FAKE-SECRET-KEY"}
    with pytest.raises(ModelError) as error:
        runtime.load_chat_model_config(settings)
    assert "FAKE-SECRET-KEY" not in str(error.value)
    assert "FAKE-SECRET-KEY" not in caplog.text


@pytest.mark.parametrize(
    "change",
    [
        {"COUNTERSIGN_LLM_PROVIDER": "another-provider"},
        {"COUNTERSIGN_LLM_MODEL": "gemini model\nunsafe"},
        {"COUNTERSIGN_LLM_MODEL": "prefix-FAKE-SECRET-KEY"},
        {"GOOGLE_API_KEY": "FAKE-SECRET-KEY\nunsafe"},
        {"COUNTERSIGN_LLM_MODEL": 42},
        {"GOOGLE_API_KEY": None},
    ],
)
def test_unsupported_or_malformed_values_fail_with_fixed_errors(change):
    with pytest.raises(ModelError) as error:
        runtime.load_chat_model_config({**SETTINGS, **change})
    assert "FAKE-SECRET-KEY" not in str(error.value)


def test_keys_are_not_an_implicit_model_selection():
    with pytest.raises(ModelError, match="MODEL_PROVIDER_AND_ID_REQUIRED"):
        runtime.load_chat_model_config({"GOOGLE_API_KEY": "FAKE-SECRET-KEY"})


def test_explicit_provider_and_key_use_the_owner_selected_default():
    config = runtime.load_chat_model_config(
        {
            "COUNTERSIGN_LLM_PROVIDER": "google_genai",
            "GOOGLE_API_KEY": "FAKE-SECRET-KEY",
        }
    )
    assert config.model == "gemini-3.5-flash"


def test_eval_fallback_is_selected_explicitly_and_never_automatic():
    config = runtime.load_chat_model_config(
        {
            **SETTINGS,
            "COUNTERSIGN_LLM_MODEL": "gemini-3.5-flash-lite",
        }
    )
    assert config.model == "gemini-3.5-flash-lite"
    assert runtime.load_chat_model_config(SETTINGS).model == "gemini-3.5-flash"


def test_unrelated_environment_values_are_ignored():
    assert runtime.load_chat_model_config({"UNRELATED_MODEL_KEY": "FAKE-SECRET-KEY"}) is None
