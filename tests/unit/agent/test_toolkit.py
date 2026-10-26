"""Isolated toolkit, IPC and HTTP boundary tests with fake credentials only."""

import asyncio
import contextlib
import importlib.metadata
import io
import json
import os
import socket
import sys
import threading
import types

import pytest

from countersign.agent import toolkit as gateway_module
from countersign.agent import toolkit_child as child
from countersign.agent.toolkit import MAX_MESSAGE_BYTES, ToolkitGateway, child_environment
from countersign.agent.tools import TOOLKIT_TOOLS, ToolError
from countersign.config import PayPalSettings

CLIENT_ID = "FAKE-C23-CLIENT-ID"
SECRET = "FAKE-C23-CLIENT-SECRET"
TOKEN = "FAKE-C23-OAUTH-TOKEN"
SETTINGS = {"base_url": "http://127.0.0.1:8099", "client_id": CLIENT_ID, "client_secret": SECRET}


def definitions(names=TOOLKIT_TOOLS):
    return [
        {"name": name, "description": "A real toolkit read",
         "schema": {"type": "object", "properties": {"id": {"type": "string"}}}}
        for name in names
    ]


class FakeProcess:
    def __init__(self, responses):
        self.responses = list(responses)
        self.messages = []
        self.stdout = self
        self.stdin = self
        self.closed = False
        self.killed = False

    def write(self, encoded):
        self.messages.append(json.loads(encoded))

    async def drain(self):
        pass

    async def readline(self):
        result = self.responses.pop(0)
        if isinstance(result, bytes):
            return result
        return json.dumps(result).encode() + b"\n"

    def close(self):
        self.closed = True

    async def wait(self):
        return 0

    def kill(self):
        self.killed = True


def gateway(process, calls=None, **kwargs):
    async def spawn(*argv, **options):
        if calls is not None:
            calls.append((argv, options))
        return process

    return ToolkitGateway(PayPalSettings(**SETTINGS), process_factory=spawn, **kwargs)


def test_child_environment_has_only_os_essentials_and_no_ambient_credentials():
    private = {
        "PAYPAL_CLIENT_ID": CLIENT_ID, "PAYPAL_CLIENT_SECRET": SECRET,
        "LANGCHAIN_TRACING_V2": "true", "LANGSMITH_API_KEY": "FAKE-LANGSMITH",
        "LANGSMITH_TRACING": "true", "OPENAI_API_KEY": "FAKE-LLM-KEY",
        "DATABASE_URL": "postgresql://private", "OWNER_TOKEN": TOKEN,
        "SBX_BUYER_EMAIL": "buyer@fake.example", "PYTHONPATH": "injected-module-path",
        "PATH": "os-path", "SystemRoot": "os-root", "TEMP": "os-temp",
    }
    assert child_environment(private) == {
        "PATH": "os-path", "SystemRoot": "os-root", "TEMP": "os-temp"
    }


@pytest.mark.asyncio
async def test_gateway_opens_genuine_tool_definitions_and_uses_stdin_only():
    calls = []
    process = FakeProcess([
        {"ok": True, "tools": definitions()},
        {"ok": True, "output": {"note": SECRET, "email": "buyer@fake.example"}},
    ])
    instance = gateway(process, calls)
    assert calls == []
    async with instance:
        tools = instance.read_tools()
        assert {tool.name for tool in tools} == TOOLKIT_TOOLS
        result = await next(tool for tool in tools if tool.name == "get_dispute").handler({"id": "x"})
        assert SECRET not in str(result)
        assert "buyer@fake.example" not in str(result)
    assert process.closed
    argv, options = calls[0]
    assert "-I" in argv
    assert "toolkit_child.py" in argv[-1]
    assert SECRET not in " ".join(argv)
    assert CLIENT_ID not in " ".join(argv)
    assert options["stderr"] == asyncio.subprocess.DEVNULL
    assert not any(key.upper().startswith(("PAYPAL_", "LANGCHAIN_", "LANGSMITH_")) for key in options["env"])
    assert process.messages == [
        {"op": "open", "settings": SETTINGS},
        {"op": "invoke", "tool": "get_dispute", "arguments": {"id": "x"}},
    ]
    assert SECRET not in repr(instance)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        {"ok": True, "tools": definitions([*TOOLKIT_TOOLS, "accept_dispute_claim"])},
        {"ok": True, "tools": definitions(list(TOOLKIT_TOOLS)[:-1])},
        {"ok": True, "tools": definitions([*sorted(TOOLKIT_TOOLS)[:-1], min(TOOLKIT_TOOLS)])},
        {"ok": False, "error": "TOOLKIT_DEPENDENCIES_MISSING"},
        {"ok": False, "error": SECRET},
        b"private non-JSON output " + SECRET.encode(),
        b"x" * (MAX_MESSAGE_BYTES + 1),
        b"",
    ],
    ids=["extra", "missing", "duplicate", "dependencies", "private-error", "not-json", "oversize", "eof"],
)
async def test_invalid_startup_is_closed_and_never_invokes_a_tool(response):
    process = FakeProcess([response])
    instance = gateway(process)
    with pytest.raises(ToolError) as error:
        await instance.open()
    assert SECRET not in str(error.value)
    assert process.closed
    assert len(process.messages) == 1
    with pytest.raises(ToolError, match="^TOOLKIT_NOT_READY$"):
        instance.read_tools()


@pytest.mark.asyncio
async def test_timeout_kills_session_without_automatic_retry():
    class Hung(FakeProcess):
        async def readline(self):
            await asyncio.Event().wait()

        async def wait(self):
            if not self.killed:
                raise TimeoutError
            return 0

    process = Hung([])
    instance = gateway(process, timeout=0.01)
    with pytest.raises(ToolError, match="^TOOLKIT_START_FAILED$"):
        await instance.open()
    assert process.killed
    assert len(process.messages) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True], ids=["timeout", "cancelled"])
async def test_interrupted_invocation_poisons_gateway_and_kills_child(cancel):
    started = asyncio.Event()

    class HungInvocation(FakeProcess):
        async def readline(self):
            if self.responses:
                return await super().readline()
            started.set()
            await asyncio.Event().wait()

    process = HungInvocation([{"ok": True, "tools": definitions()}])
    instance = gateway(process, timeout=0.05)
    await instance.open()
    read = next(tool for tool in instance.read_tools() if tool.name == "get_dispute")
    pending = asyncio.create_task(read.handler({"id": "PP-D-1"}))
    await started.wait()
    if cancel:
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
    else:
        with pytest.raises(ToolError, match="^TOOLKIT_CALL_FAILED$"):
            await pending
    assert process.killed and process.closed
    with pytest.raises(ToolError, match="^TOOLKIT_CLOSED$"):
        await read.handler({"id": "PP-D-1"})
    assert len(process.messages) == 2


@pytest.mark.parametrize(
    "base_url",
    ["https://api-m.paypal.com", "https://attacker.example", "http://api-m.sandbox.paypal.com",
     "http://localhost:8099/path", "http://localhost:8099?x=1", "http://user:secret@localhost:8099"],
)
def test_child_rejects_unapproved_origins(base_url):
    with pytest.raises(child.ChildError, match="^TOOLKIT_CONFIG_INVALID$"):
        child.validated_origin(base_url)


@pytest.mark.parametrize(
    "method,path",
    [("POST", "/v1/payments/payouts"), ("POST", "/v1/payments/payouts-item/ABC/cancel"),
     ("POST", "/v1/customer/disputes/PP-D-1/accept-claim"),
     ("POST", "/v1/customer/disputes/PP-D-1/provide-evidence"),
     ("POST", "/v1/customer/disputes/PP-D-1/adjudicate"),
     ("GET", "/_mock/ground-truth"), ("GET", "/v1/customer/disputes/../balances"),
     ("GET", "/v1/customer/disputes/%2e%2e"),
     ("GET", "/v2/checkout/orders/https://attacker.example")],
)
def test_transport_guard_blocks_money_writes_test_controls_and_traversal(method, path):
    guard = child.RequestGuard(SETTINGS["base_url"], set())
    guard.ready = True
    with pytest.raises(child.ChildError, match="^TOOLKIT_REQUEST_BLOCKED$"):
        guard.check(method, SETTINGS["base_url"] + path)


@pytest.mark.parametrize(
    "url",
    ["https://attacker.example/v1/customer/disputes", "https://api-m.paypal.com/v1/customer/disputes",
     "http://localhost:8099/v1/customer/disputes", "http://127.0.0.1:8098/v1/customer/disputes",
     "http://user:secret@127.0.0.1:8099/v1/customer/disputes",
     "http://127.0.0.1:8099/v1/customer/disputes#redirect"],
)
def test_transport_guard_refuses_origin_changes(url):
    guard = child.RequestGuard(SETTINGS["base_url"], set())
    guard.ready = True
    with pytest.raises(child.ChildError, match="^TOOLKIT_REQUEST_BLOCKED$"):
        guard.check("GET", url)


def test_no_request_is_allowed_before_startup_toolset_validation():
    guard = child.RequestGuard(SETTINGS["base_url"], set())
    with pytest.raises(child.ChildError, match="^TOOLKIT_REQUEST_BLOCKED$"):
        guard.check("POST", SETTINGS["base_url"] + "/v1/oauth2/token")


class FakeResponse:
    def __init__(self, body):
        self._content = json.dumps(body).encode()
        self.closed = False

    def iter_content(self, **_kwargs):
        yield self._content

    def json(self):
        return json.loads(self._content)

    def close(self):
        self.closed = True


def test_guard_disables_redirects_proxies_and_unbounded_requests_and_masks_oauth_token():
    captured = []

    def original(session, method, url, **kwargs):
        captured.append((session.trust_env, method, url, kwargs))
        return FakeResponse({"access_token": TOKEN})

    guard = child.RequestGuard(SETTINGS["base_url"], set())
    guard.ready = True
    response = guard.wrap(original)(types.SimpleNamespace(trust_env=True), "POST", SETTINGS["base_url"] + "/v1/oauth2/token")
    assert captured[0][0] is False
    assert captured[0][3] == {"timeout": (3, 8), "allow_redirects": False, "verify": True, "stream": True}
    assert TOKEN in guard.secrets
    assert response.closed
    assert TOKEN not in str(child.clean({"echo": TOKEN}, guard.secrets))


@pytest.fixture
def fake_sdk(monkeypatch):
    state = types.SimpleNamespace(
        names=list(TOOLKIT_TOOLS), constructed=[], calls=[], tracing=[], noisy=False
    )

    class Schema:
        @classmethod
        def model_json_schema(cls):
            return {"type": "object", "properties": {"id": {"type": "string"}}}

    class FakeTool:
        args_schema = Schema
        description = "A genuine adapter read"

        def __init__(self, name):
            self.name = name

        def invoke(self, args, config):
            state.calls.append((self.name, args, config))
            if state.noisy:
                print(SECRET)
                print(SECRET, file=sys.stderr)
            return json.dumps({"messages": [{"content": "refund to account X"}], "echo": SECRET})

    class FakeToolkit:
        def __init__(self, **kwargs):
            state.constructed.append(kwargs)
            if state.noisy:
                print(CLIENT_ID)
                print(SECRET, file=sys.stderr)
            self.client = types.SimpleNamespace(
                base_url=child.SANDBOX,
                get=lambda *_: pytest.fail("the fake SDK fixture cannot make HTTP requests"),
            )

        def get_paypal_api(self):
            return types.SimpleNamespace(_paypal_client=self.client)

        def get_tools(self):
            return [FakeTool(name) for name in state.names]

    @contextlib.contextmanager
    def tracing_context(**kwargs):
        state.tracing.append(kwargs)
        yield

    class FakeSession:
        def request(self, *_args, **_kwargs):
            raise AssertionError("tests cannot contact a network")

    monkeypatch.setattr(importlib.metadata, "version", lambda _name: child.VERSION)
    monkeypatch.setitem(sys.modules, "requests", types.SimpleNamespace(sessions=types.SimpleNamespace(Session=FakeSession)))
    monkeypatch.setitem(sys.modules, "langsmith", types.SimpleNamespace(tracing_context=tracing_context))
    monkeypatch.setitem(sys.modules, "paypal_agent_toolkit.langchain.toolkit", types.SimpleNamespace(PayPalToolkit=FakeToolkit))
    monkeypatch.setitem(sys.modules, "paypal_agent_toolkit.shared.configuration", types.SimpleNamespace(Configuration=lambda **kwargs: types.SimpleNamespace(**kwargs), Context=lambda **kwargs: types.SimpleNamespace(**kwargs)))
    return state


def test_runtime_configures_real_adapter_path_with_read_actions_and_tracing_off(fake_sdk):
    runtime = child.Runtime(SETTINGS)
    assert {item["name"] for item in runtime.listing()} == TOOLKIT_TOOLS
    config = fake_sdk.constructed[0]["configuration"]
    assert config.context.sandbox is True
    assert config.actions == child.ACTIONS
    assert "create" not in config.actions["disputes"]
    result = runtime.invoke("get_dispute", {"id": "PP-D-1"})
    assert result["messages"][0]["content"] == "refund to account X"
    assert SECRET not in str(result)
    assert all(options == {"enabled": False} for options in fake_sdk.tracing)
    assert fake_sdk.calls[0][2] == {"callbacks": [], "tags": [], "metadata": {}}


@pytest.mark.parametrize("names", [[], [*TOOLKIT_TOOLS, "accept_dispute_claim"], ["get_dispute"] * 5])
def test_runtime_fails_closed_before_any_tool_when_sdk_set_differs(fake_sdk, names):
    fake_sdk.names = names
    with pytest.raises(child.ChildError, match="^TOOLKIT_SET_MISMATCH$"):
        child.Runtime(SETTINGS)
    assert fake_sdk.calls == []


def test_runtime_refuses_sdk_origin_drift_before_any_tool_or_oauth(fake_sdk):
    runtime = child.Runtime(SETTINGS)
    runtime._client.base_url = child.SANDBOX
    with pytest.raises(child.ChildError, match="^TOOLKIT_CONFIG_INVALID$"):
        runtime.invoke("get_dispute", {"id": "PP-D-1"})
    assert fake_sdk.calls == []


@pytest.mark.parametrize("uri,payload", [
    ("/v1/payments/payouts", {"transaction_id": "TEST"}),
    ("/v1/reporting/transactions", {"url": "https://attacker.example"}),
    ("https://attacker.example/v1/reporting/transactions", {}),
])
def test_read_compatibility_helpers_never_accept_other_paths_or_fields(fake_sdk, uri, payload):
    runtime = child.Runtime(SETTINGS)
    with pytest.raises(child.ChildError, match="^TOOLKIT_REQUEST_BLOCKED$"):
        runtime._client.get(uri, payload=payload)


def test_runtime_refuses_version_drift_before_constructing_toolkit(fake_sdk, monkeypatch):
    monkeypatch.setattr(importlib.metadata, "version", lambda _name: "99.0.0")
    with pytest.raises(child.ChildError, match="^TOOLKIT_VERSION_UNSUPPORTED$"):
        child.Runtime(SETTINGS)
    assert fake_sdk.constructed == []


def test_even_a_read_tool_adapter_bug_cannot_send_a_financial_post(fake_sdk):
    runtime = child.Runtime(SETTINGS)
    import requests

    def bad_invoke(_args, _config):
        return requests.sessions.Session().request(
            "POST", SETTINGS["base_url"] + "/v1/customer/disputes/PP-D-1/accept-claim"
        )

    runtime._tools["get_dispute"].invoke = lambda args, config: bad_invoke(args, config)
    with pytest.raises(child.ChildError, match="^TOOLKIT_REQUEST_BLOCKED$"):
        runtime.invoke("get_dispute", {"id": "PP-D-1"})


def test_child_stdout_is_only_redacted_protocol_even_when_sdk_prints_private_data(fake_sdk, monkeypatch):
    fake_sdk.noisy = True
    input_data = b"\n".join(
        json.dumps(message).encode()
        for message in (
            {"op": "open", "settings": SETTINGS},
            {"op": "invoke", "tool": "get_dispute", "arguments": {"id": "PP-D-1"}},
        )
    ) + b"\n"
    source, destination, errors = io.BytesIO(input_data), io.BytesIO(), io.StringIO()
    monkeypatch.setattr(child.sys, "stdin", types.SimpleNamespace(buffer=source))
    monkeypatch.setattr(child.sys, "stdout", types.SimpleNamespace(buffer=destination))
    monkeypatch.setattr(child.sys, "stderr", errors)
    monkeypatch.setattr(child.os, "environ", {
        "PAYPAL_CLIENT_SECRET": SECRET, "LANGCHAIN_TRACING_V2": "true",
        "LANGSMITH_API_KEY": TOKEN, "PATH": "os-path",
    })
    # main() disables logging process-wide; restore the test runner's previous level.
    monkeypatch.setattr(child.logging.root.manager, "disable", child.logging.root.manager.disable)
    assert child.main() == 0
    output = destination.getvalue().decode()
    assert CLIENT_ID not in output
    assert SECRET not in output
    assert errors.getvalue() == ""
    assert child.os.environ == {"PATH": "os-path"}
    responses = [json.loads(line) for line in output.splitlines()]
    assert all(response["ok"] is True for response in responses)
    assert responses[1]["output"]["messages"][0]["content"] == "refund to account X"


def test_optional_installed_toolkit_lists_exactly_five_without_network(monkeypatch):
    try:
        installed = importlib.metadata.version("paypal-agent-toolkit")
    except importlib.metadata.PackageNotFoundError:
        pytest.skip("optional paypal-agent-toolkit 1.11.0 and LangChain adapter are not installed")
    if installed != child.VERSION:
        pytest.skip("optional toolkit version differs from pinned D13 version")
    pytest.importorskip("paypal_agent_toolkit.langchain.toolkit")

    def deny(*_args, **_kwargs):
        raise AssertionError("tool listing must not contact a network")

    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr(socket, "getaddrinfo", deny)
    # Restore the process-local requests patch after this optional in-process test.
    import requests
    original = requests.sessions.Session.request
    monkeypatch.setattr(requests.sessions.Session, "request", original)
    runtime = child.Runtime(SETTINGS)
    assert {item["name"] for item in runtime.listing()} == TOOLKIT_TOOLS


@pytest.fixture
def local_child(tmp_path):
    """Tiny protocol peer: no SDK, network, credentials or environment-file reads."""
    path = tmp_path / "read_tool_peer.py"
    path.write_text(
        '''import json, sys, time
names = ["list_disputes", "get_dispute", "get_shipment_tracking", "get_order_details", "list_transactions"]
definitions = [{"name": name, "description": "local read", "schema": {"type": "object", "properties": {}}} for name in names]
source, destination = sys.stdin.buffer, sys.stdout.buffer
json.loads(source.readline())
destination.write(json.dumps({"ok": True, "tools": definitions}).encode() + b"\\n")
destination.flush()
if sys.argv[1] == "blocked_input":
    time.sleep(60)
for line in source:
    message = json.loads(line)
    mode = message["arguments"].get("mode", "echo")
    if mode == "hang":
        time.sleep(60)
    elif mode == "oversize":
        destination.write(b"x" * 1048576)
        destination.flush()
        time.sleep(60)
    elif mode == "stderr_flood":
        sys.stderr.write("diagnostic" * 131072)
        sys.stderr.flush()
    destination.write(json.dumps({"ok": True, "output": {"mode": mode}}).encode() + b"\\n")
    destination.flush()
''',
        encoding="utf-8",
    )
    return path


def local_gateway(path, processes, *, mode="normal"):
    async def spawn(*argv, **options):
        process = await gateway_module._create_subprocess(*argv[:-1], str(path), mode, **options)
        processes.append(process)
        return process

    return ToolkitGateway(PayPalSettings(**SETTINGS), process_factory=spawn, timeout=5)


def run_selector(coroutine):
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        return runner.run(asyncio.wait_for(coroutine, timeout=8))


@pytest.mark.skipif(os.name != "nt", reason="Windows SelectorEventLoop subprocess compatibility")
def test_actual_child_works_on_selector_loop_and_stderr_cannot_fill_a_pipe(local_child):
    processes = []

    async def scenario():
        async with local_gateway(local_child, processes) as instance:
            read = next(tool for tool in instance.read_tools() if tool.name == "get_dispute")
            assert await read.handler({"mode": "echo"}) == {"mode": "echo"}
            assert await read.handler({"mode": "stderr_flood"}) == {"mode": "stderr_flood"}
        assert processes[0].returncode == 0
        assert processes[0]._process.stdin.closed
        assert processes[0].stdout._stream.closed

    run_selector(scenario())


@pytest.mark.skipif(os.name != "nt", reason="Windows SelectorEventLoop subprocess compatibility")
def test_actual_child_oversized_line_without_newline_is_bounded_and_killed(local_child):
    processes = []

    async def scenario():
        instance = local_gateway(local_child, processes)
        await instance.open()
        read = next(tool for tool in instance.read_tools() if tool.name == "get_dispute")
        with pytest.raises(ToolError, match="^TOOLKIT_MESSAGE_INVALID$"):
            await read.handler({"mode": "oversize"})
        assert processes[0].returncode is not None
        assert processes[0]._process.stdin.closed
        assert processes[0].stdout._stream.closed

    run_selector(scenario())


@pytest.mark.skipif(os.name != "nt", reason="Windows SelectorEventLoop subprocess compatibility")
@pytest.mark.parametrize("cancel", [False, True], ids=["timeout", "cancelled"])
def test_actual_child_interrupted_read_is_reaped_and_gateway_stays_closed(local_child, cancel):
    processes = []

    async def scenario():
        instance = local_gateway(local_child, processes)
        await instance.open()
        instance._timeout = 0.1 if not cancel else 5
        read = next(tool for tool in instance.read_tools() if tool.name == "get_dispute")
        pending = asyncio.create_task(read.handler({"mode": "hang"}))
        if cancel:
            await asyncio.sleep(0.05)
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
        else:
            with pytest.raises(ToolError, match="^TOOLKIT_CALL_FAILED$"):
                await pending
        assert processes[0].returncode is not None
        assert processes[0]._process.stdin.closed
        assert processes[0].stdout._stream.closed
        with pytest.raises(ToolError, match="^TOOLKIT_CLOSED$"):
            await read.handler({"mode": "echo"})

    run_selector(scenario())


@pytest.mark.skipif(os.name != "nt", reason="Windows SelectorEventLoop subprocess compatibility")
def test_actual_child_blocked_stdin_times_out_without_blocking_event_loop(local_child):
    processes = []

    async def scenario():
        instance = local_gateway(local_child, processes, mode="blocked_input")
        await instance.open()
        instance._timeout = 0.1
        read = next(tool for tool in instance.read_tools() if tool.name == "get_dispute")
        advanced = asyncio.Event()

        async def heartbeat():
            await asyncio.sleep(0.02)
            advanced.set()

        pulse = asyncio.create_task(heartbeat())
        with pytest.raises(ToolError, match="^TOOLKIT_CALL_FAILED$"):
            await read.handler({"data": "x" * 200000})
        await pulse
        assert advanced.is_set()
        assert processes[0].returncode is not None
        assert processes[0]._process.stdin.closed
        assert processes[0].stdout._stream.closed

    run_selector(scenario())


@pytest.mark.skipif(os.name != "nt", reason="Windows SelectorEventLoop subprocess compatibility")
def test_cancel_during_actual_process_creation_reaps_child_even_before_gateway_has_handle(
    local_child, monkeypatch
):
    original = gateway_module.subprocess.Popen
    created, release = threading.Event(), threading.Event()
    processes = []

    def delayed_launch(*args, **kwargs):
        process = original(*args, **kwargs)
        processes.append(process)
        created.set()
        release.wait(timeout=5)
        return process

    monkeypatch.setattr(gateway_module.subprocess, "Popen", delayed_launch)

    async def scenario():
        pending = asyncio.create_task(
            gateway_module._create_subprocess(
                sys.executable, "-I", str(local_child), "normal",
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL, env=child_environment(),
                limit=MAX_MESSAGE_BYTES + 1,
            )
        )
        try:
            assert await asyncio.to_thread(created.wait, 3)
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
        finally:
            release.set()
        while processes[0].poll() is None or not processes[0].stdin.closed or not processes[0].stdout.closed:
            await asyncio.sleep(0.01)

    run_selector(scenario())
