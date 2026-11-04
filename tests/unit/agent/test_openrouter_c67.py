"""C67 real pinned adapters with invented replies; no provider connections."""

import json
import os

import httpx
import pytest

from countersign.agent.models import ChatModelConfig, LangChainModel, ModelError
from countersign.agent.openrouter import DEFAULT_MODEL, create_client
from countersign.agent.runtime import load_chat_model_config
from countersign.agent.safety import sanitize
from countersign.config import SettingsError, load_hosted_settings
from scripts.c67_secrets import openrouter_config
from scripts.llm_smoke import smoke

FAKE = "invented-private-router-key"
CONFIG = ChatModelConfig("openrouter", DEFAULT_MODEL, FAKE)


def response(message, *, finish="stop"):
    return {"id": "stub-response", "object": "chat.completion", "created": 1,
            "model": DEFAULT_MODEL, "choices": [{"index": 0, "message": message,
            "finish_reason": finish}], "usage": {"prompt_tokens": 10,
            "completion_tokens": 5, "total_tokens": 15}}


def test_router_configuration_uses_only_selected_provider_key():
    settings = {"COUNTERSIGN_LLM_PROVIDER": "openrouter", "OPEN_ROUTER_API_KEY": FAKE,
                "GOOGLE_API_KEY": "unrelated-fake-google"}
    config = load_chat_model_config(settings)
    assert config.model == DEFAULT_MODEL and config.api_key == FAKE
    with pytest.raises(ModelError, match="MODEL_API_KEY_REQUIRED"):
        load_chat_model_config({**settings, "OPEN_ROUTER_API_KEY": ""})


def test_hosted_refuses_router_key_before_other_settings(monkeypatch):
    monkeypatch.setattr(os, "environ", {"OPEN_ROUTER_API_KEY": FAKE})
    with pytest.raises(SettingsError) as error:
        load_hosted_settings()
    assert "OPEN_ROUTER_API_KEY" in str(error.value) and FAKE not in str(error.value)


def test_key_loader_never_exports_or_interpolates_other_settings(tmp_path, monkeypatch):
    source = tmp_path / "invented.settings"
    source.write_text('PAYPAL_CLIENT_SECRET=must-not-be-loaded\n'
                      'OPEN_ROUTER_API_KEY="invented-private-router-key"\n')
    monkeypatch.setattr(os, "environ", {})
    config = openrouter_config(source)
    assert config.api_key == FAKE and not os.environ
    assert FAKE not in repr(config)
    source.write_text('OPEN_ROUTER_API_KEY=${PAYPAL_CLIENT_SECRET}\n')
    with pytest.raises(ModelError, match="MODEL_API_KEY_REQUIRED"):
        openrouter_config(source)
    assert not os.environ


@pytest.mark.parametrize("content", ["", 'OPEN_ROUTER_API_KEY=\n',
    'OPEN_ROUTER_API_KEY=one\nOPEN_ROUTER_API_KEY=two\n'])
def test_key_loader_missing_or_duplicate_is_value_free(tmp_path, content):
    source = tmp_path / "invented.settings"
    source.write_text(content)
    with pytest.raises(ModelError, match="^MODEL_API_KEY_REQUIRED$"):
        openrouter_config(source)


def test_router_keys_redact_in_free_text_and_named_fields():
    fake_token = "sk-or-v1-" + "a" * 64
    output = json.dumps(sanitize({"OPEN_ROUTER_API_KEY": FAKE,
        "note": f"OPEN_ROUTER_API_KEY={FAKE} bare {fake_token}"}))
    assert FAKE not in output and fake_token not in output


@pytest.mark.asyncio
async def test_real_adapter_preserves_complete_signed_message_and_clears_history():
    requests = []
    assistant = {"role": "assistant", "content": None,
        "reasoning_details": [{"type": "reasoning.encrypted", "data": "opaque-test-only",
                               "format": "google-gemini-v1", "index": 0}],
        "tool_calls": [{"id": "read-once", "type": "function",
            "function": {"name": "read_demo_status", "arguments": "{}"},
            "extra_content": {"google": {"thought_signature": "opaque-test-only"}}}]}

    def respond(request):
        assert request.url.host == "openrouter.ai"
        payload = json.loads(request.content)
        requests.append(payload)
        if len(requests) == 1:
            return httpx.Response(200, json=response(assistant, finish="tool_calls"))
        assert payload["messages"][-2] == assistant
        assert payload["messages"][-1]["role"] == "tool"
        assert payload["messages"][-1]["tool_call_id"] == "read-once"
        return httpx.Response(200, json=response({"role": "assistant",
            "content": '{"status":"ready"}'}))

    clients = []

    def factory(config):
        client = create_client(config, transport=httpx.MockTransport(respond))
        clients.append(client)
        return client

    result = await smoke(CONFIG, factory=factory, round_trip=True)
    assert len(requests) == 2 and result["tool_round_trip"] is True
    assert result["reported_models"] == [DEFAULT_MODEL]
    assert result["tokens"] == {"input_tokens": 20, "output_tokens": 10, "total_tokens": 30}
    assert not clients[0].history and clients[0].http.is_closed
    assert "opaque-test-only" not in json.dumps(result) and FAKE not in json.dumps(result)


@pytest.mark.asyncio
async def test_router_provider_failure_does_not_disclose_response_or_key():
    def respond(request):
        return httpx.Response(401, json={"error": {"message": FAKE}})

    model = LangChainModel(CONFIG, factory=lambda c:
                           create_client(c, transport=httpx.MockTransport(respond)))
    try:
        with pytest.raises(ModelError, match="^MODEL_CALL_FAILED$") as error:
            await model.complete({"system": "trusted", "data": {},
                                  "observations": [], "tools": []})
        assert FAKE not in str(error.value) and not model._client.history
    finally:
        await model.aclose()
