"""A tiny model boundary, with scripted tests and one optional LangChain entry."""

import asyncio
import copy
import inspect
import json
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Protocol

from countersign.agent.params import ACTION_PARAMS
from countersign.agent.safety import sanitize

MODEL_ATTEMPTS = 3  # langchain-google-genai treats max_retries as total attempts


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

    @classmethod
    def from_environment(cls, environment=None):
        """Load explicit runtime settings without opening an environment file."""
        from countersign.agent.runtime import load_chat_model_config

        return load_chat_model_config(environment)

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
        # Model calls only read and propose, so retrying a transient 429/5xx is safe;
        # the first live Gemini 3.5 Flash call took over 30 s and overloads return 503.
        max_retries=MODEL_ATTEMPTS,
        timeout=90,
        **({"vertexai": False} if config.provider == "google_genai" else {}),
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
        self._conversation = None
        self._proposal_turn = None
        self._call_lock = asyncio.Lock()
        self.last_usage_metadata = {}

    def __repr__(self):
        return "LangChainModel(values=<REDACTED>)"

    async def prepare(self) -> None:
        """Check optional packages/provider setup locally, without a model call.

        Agent startup calls this before opening its other resources. Merely
        constructing settings or a model still imports no provider and does no
        I/O. Successful preparation is cached for subsequent steps.
        """
        if self._client is not None:
            return
        try:
            self.config.validate()
            with _tracing_disabled():
                client = self._factory(self.config)
                if not callable(getattr(client, "bind_tools", None)) or not callable(
                    getattr(client, "ainvoke", None)
                ):
                    raise ModelError("MODEL_CONFIGURATION_INVALID")
            self._client = client
        except ModelError:
            raise
        except ImportError:
            raise ModelError("MODEL_DEPENDENCIES_REQUIRED") from None
        except Exception:  # noqa: BLE001 - provider setup errors may contain the configured key.
            raise ModelError("MODEL_CONFIGURATION_INVALID") from None

    async def complete(self, request: dict) -> dict:
        async with self._call_lock:
            return await self._complete(request)

    async def aclose(self):
        """Close an owned provider on its calling event loop, without private errors."""
        async with self._call_lock:
            client, self._client = self._client, None
            self._conversation = None
            self._proposal_turn = None
            close = getattr(client, "aclose", None)
            if callable(close):
                try:
                    await close()
                except Exception:  # noqa: BLE001 - provider cleanup text may contain credentials.
                    raise ModelError("MODEL_CLOSE_FAILED") from None

    def finish_step(self):
        """Discard signed history after a validated proposal or any step failure."""
        self._conversation = None
        self._proposal_turn = None

    def _messages(self, clean):
        """Retain original assistant messages, including opaque Gemini signatures.

        Only the current investigation is retained. An unexpected continuation
        fails closed rather than mixing incidents or reconstructing signatures.
        """
        observations = clean["observations"]
        identity = json.dumps(
            {key: clean[key] for key in ("system", "data", "tools")}, sort_keys=True
        )
        if not observations:
            self.finish_step()
            return identity, [
                ("system", clean["system"]),
                ("human", json.dumps({"untrusted_data": clean["data"], "observations": []})),
            ]
        previous = self._conversation
        if previous is None and self._proposal_turn is not None:
            previous, self._proposal_turn = self._proposal_turn, None
            seen = previous["observations"]
            if (previous["identity"] != identity or len(observations) != seen + 1
                    or observations[:seen] != previous["prior_observations"]
                    or type(observations[-1]) is not dict
                    or observations[-1].keys() != {"error", "action"}
                    or observations[-1]["error"] != "INVALID_PROPOSAL_PARAMS"
                    or type(observations[-1]["action"]) is not str
                    or observations[-1]["action"] not in ACTION_PARAMS
                    or len(previous["messages"]) > 64):
                raise ModelError("MODEL_CONVERSATION_MISMATCH")
            # A proposal is an assistant text turn, not a tool call. Keep its
            # original signed message and append fixed feedback as human data;
            # never fabricate a ToolMessage/function call to carry this error.
            return identity, [*previous["messages"],
                              ("human", json.dumps({"observation": observations[-1]}))]
        if previous is None or previous["identity"] != identity:
            raise ModelError("MODEL_CONVERSATION_MISMATCH")
        seen, pending = previous["observations"], previous["calls"]
        if (
            len(observations) != seen + len(pending)
            or len(previous["messages"]) > 64
            or observations[:seen] != previous["prior_observations"]
        ):
            raise ModelError("MODEL_CONVERSATION_MISMATCH")
        from langchain_core.messages import ToolMessage

        messages = list(previous["messages"])
        for observation, call in zip(observations[seen:], pending, strict=True):
            actual = observation["tool_call"]
            if actual["id"] != call["id"] or actual["name"] != call["name"]:
                raise ModelError("MODEL_CONVERSATION_MISMATCH")
            messages.append(
                ToolMessage(
                    content=json.dumps({"untrusted_result": observation["untrusted_result"]}),
                    tool_call_id=call["id"],
                    name=call["name"],
                )
            )
        return identity, messages

    async def _complete(self, request: dict) -> dict:
        self.last_usage_metadata = {}
        try:
            await self.prepare()
            clean = sanitize(request, (self.config.api_key,))
            identity, messages = self._messages(clean)
            with _tracing_disabled():
                client = self._client.bind_tools(clean["tools"]) if clean["tools"] else self._client
                # Untrusted text is only a JSON human message. It is never
                # promoted into the trusted system prompt or a tool definition.
                response = await client.ainvoke(messages, config={"callbacks": []})
            usage = getattr(response, "usage_metadata", None)
            if isinstance(usage, Mapping):
                self.last_usage_metadata = {
                    key: usage[key]
                    for key in ("input_tokens", "output_tokens", "total_tokens")
                    if type(usage.get(key)) is int and usage[key] >= 0
                }
            if getattr(response, "invalid_tool_calls", None):
                raise ModelError("INVALID_MODEL_RESPONSE")
            calls = getattr(response, "tool_calls", None)
            if calls:
                self._conversation = {
                    "identity": identity,
                    "messages": [*messages, response],
                    "observations": len(clean["observations"]),
                    "calls": calls,
                    "prior_observations": copy.deepcopy(clean["observations"]),
                }
                return {
                    "tool_calls": [
                        {"id": call["id"], "name": call["name"], "args": call["args"]}
                        for call in calls
                    ]
                }
            content = response.content
            if isinstance(content, list):
                content = "".join(
                    block["text"]
                    for block in content
                    if isinstance(block, Mapping)
                    and block.get("type") == "text"
                    and isinstance(block.get("text"), str)
                )
            if type(content) is not str:
                raise ModelError("INVALID_MODEL_RESPONSE")
            self._conversation = None
            self._proposal_turn = {
                "identity": identity, "messages": [*messages, response],
                "observations": len(clean["observations"]),
                "prior_observations": copy.deepcopy(clean["observations"]),
            }
            # Proposal decoding (including duplicate keys) happens at the
            # commander boundary rather than accepting arbitrary provider text.
            return {"proposal_json": content}
        except ModelError:
            self.finish_step()
            raise
        except Exception:  # noqa: BLE001 - provider errors may contain credentials or prompt text.
            self.finish_step()
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
