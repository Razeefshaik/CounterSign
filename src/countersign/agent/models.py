"""A tiny model boundary, with scripted tests and one optional LangChain entry."""

import copy
import inspect
import json
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Protocol

from countersign.agent.safety import sanitize


class ModelError(RuntimeError):
    """Fixed model configuration/call errors, never provider exception text."""


class ChatModel(Protocol):
    async def complete(self, request: dict) -> dict: ...


@dataclass(frozen=True, repr=False)
class ChatModelConfig:
    """Explicit configuration only: no environment, model or key is guessed."""

    provider: str | None = None
    model: str | None = None
    api_key: str | None = field(default=None, repr=False)

    def __repr__(self):
        return "ChatModelConfig(values=<REDACTED>)"

    def validate(self):
        if any(
            type(value) is not str or not value.strip() for value in (self.provider, self.model)
        ):
            raise ModelError("MODEL_PROVIDER_AND_ID_REQUIRED")
        if type(self.api_key) is not str or not self.api_key.strip():
            raise ModelError("MODEL_API_KEY_REQUIRED")


class ScriptedModel:
    """Return a supplied sequence of model turns; never invoke a provider."""

    model_id = "scripted"

    def __init__(self, responses: Sequence[dict], *, secrets=()):
        if not isinstance(responses, Sequence) or isinstance(responses, str | bytes):
            raise ModelError("INVALID_MODEL_SCRIPT")
        self._responses = copy.deepcopy(list(responses))
        self._secrets = tuple(secrets)
        self.requests = []

    def __repr__(self):
        return "ScriptedModel(values=<REDACTED>)"

    async def complete(self, request: dict) -> dict:
        if not self._responses:
            raise ModelError("MODEL_SCRIPT_EXHAUSTED")
        self.requests.append(sanitize(request, self._secrets))
        return copy.deepcopy(self._responses.pop(0))


@contextmanager
def _tracing_disabled():
    try:
        from langsmith import tracing_context
    except ImportError:
        raise ModelError("MODEL_DEPENDENCIES_REQUIRED") from None
    with tracing_context(enabled=False):
        yield


def _langchain_factory(config):
    try:
        from langchain.chat_models import init_chat_model
    except ImportError:
        raise ModelError("MODEL_DEPENDENCIES_REQUIRED") from None
    return init_chat_model(
        config.model,
        model_provider=config.provider,
        api_key=config.api_key,
        configurable_fields=None,
        max_retries=0,
    )


class LangChainModel:
    """Optional real model. Construction is local; only complete() may call it.

    Install LangChain plus the selected provider integration separately after the
    owner picks the provider/model. No provider package is imported by scripted
    tests or when the agent is disabled.
    """

    def __init__(self, config: ChatModelConfig, *, factory=None):
        config.validate()
        self.config = config
        self.model_id = config.model
        self._factory = factory or _langchain_factory
        self._client = None

    def __repr__(self):
        return "LangChainModel(values=<REDACTED>)"

    async def complete(self, request: dict) -> dict:
        try:
            clean = sanitize(request, (self.config.api_key,))
            with _tracing_disabled():
                if self._client is None:
                    self._client = self._factory(self.config)
                client = self._client.bind_tools(clean["tools"])
                # Untrusted text is only a JSON human message. It is never
                # promoted into the trusted system prompt or a tool definition.
                messages = [
                    ("system", clean["system"]),
                    (
                        "human",
                        json.dumps(
                            {"untrusted_data": clean["data"], "observations": clean["observations"]}
                        ),
                    ),
                ]
                response = await client.ainvoke(messages, config={"callbacks": []})
            if getattr(response, "invalid_tool_calls", None):
                raise ModelError("INVALID_MODEL_RESPONSE")
            calls = getattr(response, "tool_calls", None)
            if calls:
                return {
                    "tool_calls": [
                        {"id": call["id"], "name": call["name"], "args": call["args"]}
                        for call in calls
                    ]
                }
            content = response.content
            if type(content) is not str:
                raise ModelError("INVALID_MODEL_RESPONSE")
            # Proposal decoding (including duplicate keys) happens at the
            # commander boundary rather than accepting arbitrary provider text.
            return {"proposal_json": content}
        except ModelError:
            raise
        except Exception:  # noqa: BLE001 - provider errors may contain credentials or prompt text.
            raise ModelError("MODEL_CALL_FAILED") from None


def create_model(config, *, factory=None):
    """Choose a scripted/injected protocol or the single LangChain config path."""
    if isinstance(config, ChatModelConfig):
        return LangChainModel(config, factory=factory)
    if not isinstance(config, Mapping) and inspect.iscoroutinefunction(
        getattr(config, "complete", None)
    ):
        return config
    raise ModelError("MODEL_CONFIGURATION_REQUIRED")
