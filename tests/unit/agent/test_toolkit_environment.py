"""The current model and pinned PayPal adapter run in distinct environments."""

import asyncio
import importlib.metadata
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from countersign.agent.toolkit import ToolkitGateway, child_environment
from countersign.agent.tools import TOOLKIT_TOOLS
from countersign.config import PayPalSettings


@pytest.mark.parametrize("selection", ["default", "environment", "explicit"])
def test_gateway_launches_selected_interpreter_without_inheriting_model_key(monkeypatch, selection):
    launched = []

    class Peer:
        stdin = stdout = property(lambda self: self)

        def write(self, _):
            pass

        async def drain(self):
            pass

        async def readline(self):
            return (
                json.dumps(
                    {
                        "ok": True,
                        "tools": [
                            {"name": name, "description": "read", "schema": {"type": "object"}}
                            for name in sorted(TOOLKIT_TOOLS)
                        ],
                    }
                ).encode()
                + b"\n"
            )

        def close(self):
            pass

        async def wait(self):
            return 0

    async def spawn(*argv, **options):
        launched.append((argv, options))
        return Peer()

    monkeypatch.delenv("COUNTERSIGN_TOOLKIT_PYTHON", raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", "FAKE-MODEL-KEY")
    if selection != "default":
        monkeypatch.setenv("COUNTERSIGN_TOOLKIT_PYTHON", "environment-python")
    explicit = "explicit-python" if selection == "explicit" else None

    async def scenario():
        async with ToolkitGateway(
            PayPalSettings(), python_executable=explicit, process_factory=spawn
        ):
            pass

    asyncio.run(scenario())
    expected = {
        "default": sys.executable,
        "environment": "environment-python",
        "explicit": "explicit-python",
    }[selection]
    [call] = launched
    assert call[0][0] == expected and call[0][1] == "-I"
    assert "GOOGLE_API_KEY" not in call[1]["env"]
    assert "COUNTERSIGN_TOOLKIT_PYTHON" not in call[1]["env"]


def test_genuine_legacy_toolkit_child_reads_only_the_local_stub(monkeypatch):
    executable = os.environ.get("COUNTERSIGN_TEST_TOOLKIT_PYTHON")
    if not executable:
        pytest.skip(
            "set COUNTERSIGN_TEST_TOOLKIT_PYTHON to the separate pinned toolkit environment"
        )

    # Read only package metadata; the child receives no model key or app settings.
    environment = child_environment()
    metadata = subprocess.run(
        [
            executable,
            "-I",
            "-c",
            (
                "import importlib.metadata as m,json;"
                "print(json.dumps({p:m.version(p) for p in "
                "('langchain','langchain-core','paypal-agent-toolkit')}))"
            ),
        ],
        env=environment,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    assert json.loads(metadata.stdout) == {
        "langchain": "0.3.23",
        "langchain-core": "0.3.76",
        "paypal-agent-toolkit": "1.11.0",
    }
    assert importlib.metadata.version("langchain-core") == "1.7.0"
    assert importlib.metadata.version("langchain-google-genai") == "4.4.2"

    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage, ToolMessage
    from langchain_core.outputs import ChatGeneration, ChatResult

    from countersign.agent.commander import Commander
    from countersign.agent.models import ChatModelConfig, LangChainModel
    from countersign.agent.tools import ALLOWED_TOOLS, CUSTOM_TOOLS, ReadTool, ToolRegistry

    class LocalChat(BaseChatModel):
        calls: int = 0

        @property
        def _llm_type(self):
            return "offline-child-environment-proof"

        def bind_tools(self, tools, **_):
            assert {item["function"]["name"] for item in tools} == ALLOWED_TOOLS
            return self

        def _generate(self, messages, stop=None, run_manager=None, **_):
            self.calls += 1
            if self.calls == 1:
                reply = AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "id": "read-dispute",
                            "name": "get_dispute",
                            "args": {"dispute_id": "PP-D-TEST"},
                        }
                    ],
                )
            else:
                assert self.calls == 2
                assert isinstance(messages[-1], ToolMessage)
                assert json.loads(messages[-1].content)["untrusted_result"] == {
                    "dispute_id": "PP-D-TEST",
                    "status": "OPEN",
                }
                reply = AIMessage(
                    content=json.dumps(
                        {
                            "incident_id": "incident-c23b",
                            "action": "WAIT",
                            "params": {},
                            "evidence_refs": ["dispute:PP-D-TEST"],
                            "rationale": "The locally observed dispute is open; wait for further evidence.",
                            "expected_effect": {
                                "available_change": {"minor": 0, "currency": "USD"}
                            },
                        }
                    )
                )
            return ChatResult(generations=[ChatGeneration(message=reply)])

    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def answer(self, status, body):
            encoded = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            requests.append(("POST", self.path))
            if self.path == "/v1/oauth2/token":
                self.answer(200, {"access_token": "FAKE-OAUTH-TOKEN", "token_type": "Bearer"})
            else:
                self.answer(405, {"name": "WRITE_REFUSED"})

        def do_GET(self):
            requests.append(("GET", self.path))
            if self.path == "/v1/customer/disputes/PP-D-TEST":
                self.answer(200, {"dispute_id": "PP-D-TEST", "status": "OPEN"})
            else:
                self.answer(404, {"name": "NOT_FOUND"})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("COUNTERSIGN_TOOLKIT_PYTHON", executable)
    monkeypatch.setenv("GOOGLE_API_KEY", "FAKE-NOT-FOR-THE-CHILD")

    async def scenario():
        settings = PayPalSettings(
            f"http://127.0.0.1:{server.server_port}", "fake-client", "fake-secret"
        )
        async with ToolkitGateway(settings) as gateway:
            tools = {tool.name: tool for tool in gateway.read_tools()}
            assert tools.keys() == TOOLKIT_TOOLS

            async def unused(_):
                pytest.fail("this investigation needs only the genuine dispute read")

            registry = ToolRegistry(
                [
                    *tools.values(),
                    *[
                        ReadTool(name, "unused read", {"type": "object"}, unused)
                        for name in sorted(CUSTOM_TOOLS)
                    ],
                ]
            )
            client = LocalChat()
            model = LangChainModel(
                ChatModelConfig("google_genai", "gemini-test", "FAKE-MODEL-KEY"),
                factory=lambda _: client,
            )
            proposal = await Commander(model, registry).step({"id": "incident-c23b"})
            assert proposal.action == "WAIT" and client.calls == 2

    try:
        with asyncio.Runner(
            loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None
        ) as runner:
            runner.run(scenario())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert requests == [
        ("POST", "/v1/oauth2/token"),
        ("GET", "/v1/customer/disputes/PP-D-TEST"),
    ]
