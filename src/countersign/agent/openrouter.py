"""C67: OpenAI-compatible Gemini client, preserving opaque assistant messages.

Pinned ChatOpenAI drops OpenRouter reasoning_details and tool extra_content.
HTTP hooks replay the original assistant message, only within the current step.
No signature/reasoning is exported to a trace, log, proposal or public record.
"""

import copy
import json
from contextvars import ContextVar

from countersign.agent.models import MODEL_ATTEMPTS, ModelError

BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "google/gemini-3.5-flash"
# C67: only the owner-operated runner installs a persistent budget/usage meter.
ACTIVE_BUDGET = ContextVar("c67_budget", default=None)


def message_key(message):
    return json.dumps([
        message.get("content") or "",
        [call["id"] for call in message.get("tool_calls", [])],
    ], sort_keys=True)


class OpenRouterClient:
    """Share one owned HTTP client and ephemeral history with bound tool views."""

    def __init__(self, client, http, history, metadata):
        self.client, self.http = client, http
        self.history, self.metadata = history, metadata

    def bind_tools(self, tools):
        return OpenRouterClient(self.client.bind_tools(tools), self.http,
                                self.history, self.metadata)

    async def ainvoke(self, messages, config):
        return await self.client.ainvoke(messages, config=config)

    def finish_step(self):
        self.history.clear()

    async def aclose(self):
        self.finish_step()
        await self.http.aclose()

    def __repr__(self):
        return "OpenRouterClient(values=<REDACTED>)"


def create_client(config, *, transport=None):
    import httpx
    from langchain.chat_models import init_chat_model

    history, metadata = {}, {"reported_models": set(), "http_attempts": 0}

    async def replay(request):
        if str(request.url) != BASE_URL + "/chat/completions":
            raise ModelError("MODEL_ENDPOINT_REFUSED")
        payload = json.loads(request.content)
        for index, message in enumerate(payload["messages"]):
            if message.get("role") == "assistant":
                original = history.get(message_key(message))
                if original is None:
                    raise ModelError("MODEL_CONVERSATION_MISMATCH")
                payload["messages"][index] = copy.deepcopy(original)
        budget = ACTIVE_BUDGET.get()
        if budget is not None:
            budget.before_request(payload)
        content = json.dumps(payload).encode()
        request.stream = httpx.ByteStream(content)
        request._content = content
        request.headers["content-length"] = str(len(content))
        metadata["http_attempts"] += 1

    async def capture(response):
        await response.aread()
        budget = ACTIVE_BUDGET.get()
        if budget is not None:
            budget.after_response(response)
        if response.status_code != 200:
            return
        body = response.json()
        model = body.get("model")
        if model not in {config.model, config.model.removesuffix(":free")}:
            raise ModelError("MODEL_ID_MISMATCH")
        metadata["reported_models"].add(model)
        for choice in body.get("choices", []):
            message = choice["message"]
            if len(history) >= 64:
                raise ModelError("MODEL_CONVERSATION_MISMATCH")
            history[message_key(message)] = copy.deepcopy(message)

    http = httpx.AsyncClient(transport=transport, trust_env=False, timeout=90,
                            event_hooks={"request": [replay], "response": [capture]})
    client = init_chat_model(config.model, model_provider="openai", api_key=config.api_key,
        base_url=BASE_URL, configurable_fields=None, max_retries=MODEL_ATTEMPTS - 1,
        timeout=90, http_async_client=http, use_responses_api=False,
        extra_body={"reasoning": {"enabled": True}})
    return OpenRouterClient(client, http, history, metadata)
