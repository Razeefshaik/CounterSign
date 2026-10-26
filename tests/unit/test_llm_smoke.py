"""The owner smoke command is proved with a fake provider, never Google."""

import json
import logging
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from countersign.agent import models
from countersign.agent.models import ChatModelConfig, ModelError
from scripts import llm_smoke

CONFIG = ChatModelConfig("google_genai", "gemini-3-flash-preview", "FAKE-SECRET-KEY")


@pytest.fixture
def no_tracing(monkeypatch):
    @contextmanager
    def disabled():
        yield

    monkeypatch.setattr(models, "_tracing_disabled", disabled)


class Client:
    def __init__(self, *, tool_call=True, fail=False):
        self.calls, self.bound = 0, []
        self.tool_call, self.fail = tool_call, fail

    def bind_tools(self, tools):
        self.bound.append(tools)
        return self

    async def ainvoke(self, messages, config):
        self.calls += 1
        print("FAKE-SECRET-KEY private response")
        logging.getLogger("fake-provider").warning("FAKE-SECRET-KEY private log")
        if self.fail:
            raise RuntimeError("FAKE-SECRET-KEY private provider failure")
        return SimpleNamespace(
            tool_calls=[{"id": "read-once", "name": "read_demo_status", "args": {}}]
            if self.tool_call
            else [],
            content="FAKE-SECRET-KEY private raw response",
            usage_metadata={"input_tokens": 4, "output_tokens": 2, "total_tokens": 6},
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_call", [False, True])
async def test_smoke_uses_one_real_adapter_call_and_no_paypal(tool_call, no_tracing, capsys):
    client = Client(tool_call=tool_call)
    original_logging = logging.root.manager.disable
    result = await llm_smoke.smoke(CONFIG, factory=lambda _: client)
    assert client.calls == 1
    assert len(client.bound) == 1
    assert [t["function"]["name"] for t in client.bound[0]] == ["read_demo_status"]
    assert result["provider"] == "google_genai"
    assert result["model"] == "gemini-3-flash-preview"
    assert result["tool_call_returned"] is tool_call
    assert result["tokens"] == {"input_tokens": 4, "output_tokens": 2, "total_tokens": 6}
    assert "FAKE-SECRET-KEY" not in json.dumps(result)
    captured = capsys.readouterr()
    assert "FAKE-SECRET-KEY" not in captured.out + captured.err
    assert logging.root.manager.disable == original_logging


@pytest.mark.asyncio
async def test_usage_without_safe_token_counts_is_not_printed(no_tracing):
    class PrivateUsageClient(Client):
        async def ainvoke(self, messages, config):
            response = await super().ainvoke(messages, config)
            response.usage_metadata = {
                "input_tokens": "FAKE-SECRET-KEY",
                "output_tokens": True,
                "total_tokens": -1,
                "private": "FAKE-SECRET-KEY",
            }
            return response

    client = PrivateUsageClient()
    result = await llm_smoke.smoke(CONFIG, factory=lambda _: client)
    assert client.calls == 1
    assert "tokens" not in result and "FAKE-SECRET-KEY" not in json.dumps(result)


def test_missing_configuration_stops_before_model_construction(monkeypatch, capsys):
    monkeypatch.setattr(llm_smoke, "load_chat_model_config", lambda: None)
    monkeypatch.setattr(
        llm_smoke, "LangChainModel", lambda *_: pytest.fail("no key means no provider client")
    )
    assert llm_smoke.main([]) == 1
    assert "No model configured" in capsys.readouterr().out


def test_partial_configuration_stops_before_a_provider_client(monkeypatch, capsys):
    from countersign.agent.runtime import load_chat_model_config

    monkeypatch.setattr(
        llm_smoke,
        "load_chat_model_config",
        lambda: load_chat_model_config(
            {
                "COUNTERSIGN_LLM_PROVIDER": "google_genai",
                "COUNTERSIGN_LLM_MODEL": "gemini-3-flash-preview",
            }
        ),
    )
    monkeypatch.setattr(
        llm_smoke, "LangChainModel", lambda *_: pytest.fail("missing key cannot create a client")
    )
    assert llm_smoke.main([]) == 1
    assert capsys.readouterr().out.strip() == "MODEL_API_KEY_REQUIRED"


def test_cli_success_prints_safe_metadata_only(monkeypatch, no_tracing, capsys):
    client = Client()
    monkeypatch.setattr(llm_smoke, "load_chat_model_config", lambda *_: CONFIG)
    monkeypatch.setattr(models, "_langchain_factory", lambda _: client)
    assert llm_smoke.main([]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["tool_call_returned"] is True
    assert set(result) <= {"provider", "model", "tool_call_returned", "tokens"}
    assert client.calls == 1
    assert "FAKE-SECRET-KEY" not in json.dumps(result)


@pytest.mark.asyncio
async def test_provider_failures_are_value_free_and_logging_is_restored(no_tracing, capsys):
    client = Client(fail=True)
    previous = logging.root.manager.disable
    with pytest.raises(ModelError, match="MODEL_CALL_FAILED") as error:
        await llm_smoke.smoke(CONFIG, factory=lambda _: client)
    assert client.calls == 1
    assert "FAKE-SECRET-KEY" not in str(error.value)
    captured = capsys.readouterr()
    assert "FAKE-SECRET-KEY" not in captured.out + captured.err
    assert logging.root.manager.disable == previous


def test_cli_does_not_print_an_unexpected_private_error(monkeypatch, capsys):
    def invalid():
        raise ModelError("FAKE-SECRET-KEY private configuration error")

    monkeypatch.setattr(llm_smoke, "load_chat_model_config", invalid)
    assert llm_smoke.main([]) == 1
    captured = capsys.readouterr()
    assert captured.out.strip() == "MODEL_SMOKE_FAILED"
    assert "FAKE-SECRET-KEY" not in captured.out + captured.err
