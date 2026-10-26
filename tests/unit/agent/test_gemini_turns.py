"""Real pinned LangChain/Google code, with every model HTTP request stubbed."""

import base64
import copy
import json
import socket
from typing import ClassVar

import httpx
import pytest

from countersign.agent.commander import Commander
from countersign.agent.models import MODEL_ATTEMPTS, ChatModelConfig, LangChainModel, ModelError
from countersign.agent.tools import ToolError


@pytest.fixture
def google_stub(monkeypatch):
    google = pytest.importorskip("langchain_google_genai")
    from langchain.chat_models.base import _get_chat_model_creator

    _get_chat_model_creator.cache_clear()
    real_chat = google.ChatGoogleGenerativeAI
    requests, clients, replies = [], [], []

    def no_network(*_args, **_kwargs):
        pytest.fail("model tests must use the fake HTTP transport")

    # Event-loop socket pairs are local OS plumbing, not provider connections.
    connect = socket.socket.connect

    def connect_only_loopback(sock, address):
        if isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1"}:
            return connect(sock, address)
        return no_network()

    monkeypatch.setattr(socket.socket, "connect", connect_only_loopback)
    monkeypatch.setattr(socket, "create_connection", no_network)

    def respond(request):
        requests.append(json.loads(request.content))
        assert request.url.host == "generativelanguage.googleapis.com"
        assert replies, "a model call exceeded its test budget"
        status, body = replies.pop(0)
        return httpx.Response(status, json=body)

    def create(*args, **kwargs):
        assert kwargs["max_retries"] == MODEL_ATTEMPTS and kwargs["vertexai"] is False
        model = real_chat(
            *args,
            **kwargs,
            client_args={"transport": httpx.MockTransport(respond), "trust_env": False},
        )
        clients.append(model.client)
        return model

    monkeypatch.setattr(google, "ChatGoogleGenerativeAI", create)
    yield requests, clients, replies
    for client in clients:
        client.close()
    _get_chat_model_creator.cache_clear()


class Reads:
    schemas: ClassVar[list] = [
        {
            "type": "function",
            "function": {
                "name": "get_dispute",
                "description": "Read the test dispute.",
                "parameters": {
                    "type": "object",
                    "properties": {"dispute_id": {"type": "string"}},
                    "required": ["dispute_id"],
                    "additionalProperties": False,
                },
            },
        }
    ]

    def __init__(self):
        self.calls = []

    async def invoke(self, name, args):
        self.calls.append((name, args))
        assert name == "get_dispute" and args == {"dispute_id": "TEST-DISPUTE"}
        return {"status": "UNDER_REVIEW", "dispute_id": "TEST-DISPUTE"}


def response(parts):
    return {
        "candidates": [{"content": {"role": "model", "parts": parts}, "finishReason": "STOP"}],
        "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5, "totalTokenCount": 15},
    }


@pytest.mark.asyncio
async def test_commander_preserves_gemini_signature_through_a_real_tool_round(google_stub, caplog):
    requests, clients, replies = google_stub
    signature = base64.b64encode(b"opaque-test-signature").decode()
    replies.extend(
        [
            (
                200,
                response(
                    [
                        {
                            "functionCall": {
                                "name": "get_dispute",
                                "args": {"dispute_id": "TEST-DISPUTE"},
                            },
                            "thoughtSignature": signature,
                        }
                    ]
                ),
            ),
            (
                200,
                response(
                    [
                        {
                            "text": json.dumps(
                                {
                                    "incident_id": "incident-1",
                                    "action": "WAIT",
                                    "params": {},
                                    "evidence_refs": ["dispute:TEST-DISPUTE"],
                                    "rationale": "Await review.",
                                    "expected_effect": {
                                        "available_change": {"minor": 0, "currency": "USD"}
                                    },
                                }
                            ),
                            "thoughtSignature": base64.b64encode(b"final-test-signature").decode(),
                        }
                    ]
                ),
            ),
        ]
    )
    tools = Reads()
    model = LangChainModel(ChatModelConfig("google_genai", "gemini-3.5-flash", "FAKE-KEY"))
    try:
        proposal = await Commander(model, tools).step({"id": "incident-1"})
        assert proposal.action == "WAIT" and len(requests) == 2
        assert tools.calls == [("get_dispute", {"dispute_id": "TEST-DISPUTE"})]
        assistant = next(turn for turn in requests[1]["contents"] if turn["role"] == "model")
        assert assistant["parts"][0]["thoughtSignature"] == signature
        assert any(
            "functionResponse" in part for turn in requests[1]["contents"] for part in turn["parts"]
        )
        assert model.last_usage_metadata == {
            "input_tokens": 10,
            "output_tokens": 5,
            "total_tokens": 15,
        }
        assert model._conversation is None
        assert "FAKE-KEY" not in json.dumps(requests) and "FAKE-KEY" not in caplog.text
    finally:
        for client in clients:
            await client.aio.aclose()


@pytest.mark.asyncio
async def test_gemini_signatures_survive_failed_read_and_corrected_retry(google_stub):
    requests, clients, replies = google_stub
    signatures = [base64.b64encode(value).decode() for value in (b"failed-turn", b"retry-turn")]
    for identifier, signature in zip(("internal-uuid", "TEST-DISPUTE"), signatures, strict=True):
        replies.append((200, response([{
            "functionCall": {"name": "get_dispute", "args": {"dispute_id": identifier}},
            "thoughtSignature": signature,
        }])))
    replies.append((200, response([{"text": json.dumps({
        "incident_id": "incident-1", "action": "WAIT", "params": {}, "evidence_refs": [],
        "rationale": "Await confirmed review.",
        "expected_effect": {"available_change": {"minor": 0, "currency": "USD"}},
    })}])))

    class RecoveringReads(Reads):
        async def invoke(self, name, args):
            if args["dispute_id"] == "internal-uuid":
                self.calls.append((name, args))
                raise ToolError("TOOLKIT_CALL_FAILED")
            return await super().invoke(name, args)

    tools = RecoveringReads()
    model = LangChainModel(ChatModelConfig("google_genai", "gemini-3.5-flash", "FAKE-KEY"))
    try:
        assert (await Commander(model, tools).step({"id": "incident-1"})).action == "WAIT"
        assert len(requests) == 3 and len(tools.calls) == 2
        for index in (1, 2):
            parts = [part for turn in requests[index]["contents"] for part in turn["parts"]]
            assert [p["thoughtSignature"] for p in parts if "functionCall" in p] == signatures[:index]
            results = [p["functionResponse"] for p in parts if "functionResponse" in p]
            assert len(results) == index
            assert results[0]["response"]["untrusted_result"] == {
                "error": "TOOLKIT_CALL_FAILED", "tool": "get_dispute"
            }
            if index == 2:
                assert results[1]["response"]["untrusted_result"]["dispute_id"] == "TEST-DISPUTE"
        assert model._conversation is None
    finally:
        for client in clients:
            await client.aio.aclose()


@pytest.mark.asyncio
async def test_google_rate_limit_retries_boundedly_and_never_exposes_private_error(
    google_stub, caplog
):
    requests, clients, replies = google_stub
    replies.extend(
        [(429, {"error": {"code": 429, "message": "FAKE-KEY", "status": "RESOURCE_EXHAUSTED"}})]
        * MODEL_ATTEMPTS
    )
    model = LangChainModel(ChatModelConfig("google_genai", "gemini-3.5-flash", "FAKE-KEY"))
    try:
        with pytest.raises(ModelError, match="^MODEL_CALL_FAILED$") as caught:
            await model.complete({"system": "trusted", "data": {}, "observations": [], "tools": []})
        assert len(requests) == MODEL_ATTEMPTS and "FAKE-KEY" not in str(caught.value)
        assert "FAKE-KEY" not in caplog.text
        assert model._conversation is None
    finally:
        for client in clients:
            await client.aio.aclose()


@pytest.mark.asyncio
async def test_continuation_cannot_mix_incidents_or_fabricate_a_tool_response(google_stub):
    _, clients, replies = google_stub
    replies.append(
        (
            200,
            response(
                [
                    {
                        "functionCall": {
                            "name": "get_dispute",
                            "args": {"dispute_id": "TEST-DISPUTE"},
                        },
                        "thoughtSignature": base64.b64encode(b"signature").decode(),
                    }
                ]
            ),
        )
    )
    model = LangChainModel(ChatModelConfig("google_genai", "gemini-3.5-flash", "FAKE-KEY"))
    initial = {
        "system": "trusted",
        "data": {"incident": "first"},
        "observations": [],
        "tools": Reads.schemas,
    }
    try:
        first = await model.complete(initial)
        continuation = copy.deepcopy(initial)
        continuation["data"]["incident"] = "second"
        continuation["observations"] = [
            {"tool_call": first["tool_calls"][0], "untrusted_result": {}}
        ]
        with pytest.raises(ModelError, match="^MODEL_CONVERSATION_MISMATCH$"):
            await model.complete(continuation)
        assert model._conversation is None
    finally:
        for client in clients:
            await client.aio.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("model_id", ["gemini-3.5-flash", "gemini-3.5-flash-lite"])
async def test_real_adapter_preserves_signatures_across_two_tool_turns(monkeypatch, model_id):
    pytest.importorskip("langchain_google_genai")
    original_connect = socket.socket.connect

    def deny(*args, **kwargs):
        raise AssertionError("Real provider connections are forbidden")

    def connect(sock, address):
        # Windows creates a local socket pair for its event loop.
        if isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1"}:
            return original_connect(sock, address)
        return deny()

    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "false")

    class Reads:
        def __init__(self):
            self.schemas = [
                {
                    "type": "function",
                    "function": {"name": "read_demo_status", "parameters": {"type": "object"}},
                }
            ]
            self.calls = 0

        async def invoke(self, name, args):
            assert name == "read_demo_status" and args == {}
            self.calls += 1
            return {"status": "waiting"}

    reads = Reads()
    from google.genai import models, types

    signatures = [b"opaque-first-\x00\xff", b"opaque-second-\x00\xfe"]
    text_signature = b"opaque-text-\x00\xfd"
    requests = []

    async def generate_content(self, *, model, contents, config):
        assert model == model_id
        turn = len(requests)
        requests.append(contents)
        call_parts = [part for item in contents for part in item.parts if part.function_call]
        assert [part.thought_signature for part in call_parts] == signatures[:turn]
        results = [part for item in contents for part in item.parts if part.function_response]
        assert len(results) == turn == reads.calls
        if turn:
            assert any(
                part.text == "Read the local status." and part.thought_signature == text_signature
                for item in contents
                for part in item.parts
            )
            assert all(part.function_response.name == "read_demo_status" for part in results)
        if turn < 2:
            parts = []
            if turn == 0:
                parts.append(
                    types.Part(text="Read the local status.", thought_signature=text_signature)
                )
            parts.append(
                types.Part(
                    function_call=types.FunctionCall(name="read_demo_status", args={}),
                    thought_signature=signatures[turn],
                )
            )
        else:
            parts = [
                types.Part(
                    text=json.dumps(
                        {
                            "incident_id": "incident-1",
                            "action": "WAIT",
                            "params": {},
                            "evidence_refs": [],
                            "rationale": "Await confirmation.",
                            "expected_effect": {
                                "available_change": {"minor": 0, "currency": "USD"}
                            },
                        }
                    )
                )
            ]
        return types.GenerateContentResponse(
            model_version=model_id,
            candidates=[types.Candidate(content=types.Content(role="model", parts=parts))],
        )

    monkeypatch.setattr(models.AsyncModels, "generate_content", generate_content)
    # Keep CounterSign's real factory, bind_tools, ainvoke and response parsing.
    configured = LangChainModel(ChatModelConfig("google_genai", model_id, "FAKE-API-KEY"))
    try:
        result = await Commander(configured, reads).step({"id": "incident-1"})
        assert result.action == "WAIT"
        assert len(requests) == 3 and reads.calls == 2
        assert configured._conversation is None
    finally:
        await configured.aclose()
