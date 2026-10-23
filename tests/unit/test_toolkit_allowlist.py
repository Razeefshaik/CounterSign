"""Hard rule: the agent's PayPal Agent Toolkit exposes read tools only (D13, C07, C23)."""

import json
import socket
import subprocess
import sys
import tempfile
from datetime import UTC, datetime

import httpx
import pytest

from scripts.sandbox_checks.checks import d13_toolkit as d13
from scripts.sandbox_checks.env import SandboxSettings
from scripts.sandbox_checks.manual import ManualSteps
from scripts.sandbox_checks.redact import Redactor
from scripts.sandbox_checks.runner import CheckFailed, Runner

NOW = datetime(2026, 10, 24, 9, 0, tzinfo=UTC)
CLIENT_ID = "FAKE-CLIENT-ID"
SECRET = "FAKE-CLIENT-SECRET"
TOKEN = "FAKE-ACCESS-TOKEN"
BUYER = "buyer-1@fake.example"
EXACT = sorted(d13.ALLOWED_TOOLS)
ORDER_ID = "5O190127TN364715T"

# paypal-agent-toolkit 1.11.0 shared/tools.py: method -> actions, for the products we configure.
TOOLKIT_1_11_TOOLS = {
    "create_order": {"orders": {"create": True}},
    "pay_order": {"orders": {"capture": True}},
    "get_order_details": {"orders": {"get": True}},
    "list_disputes": {"disputes": {"list": True}},
    "get_dispute": {"disputes": {"get": True}},
    "accept_dispute_claim": {"disputes": {"create": True}},
    "create_shipment_tracking": {"shipment": {"create": True}},
    "get_shipment_tracking": {"shipment": {"get": True}},
    "update_shipment_tracking": {"shipment": {"update": True}},
    "list_transactions": {"transactions": {"list": True}},
}


# --- pure checks (the C23 agent startup check reuses these) ---


def test_exact_set_is_accepted_in_any_order():
    d13.check_allowlist(reversed(EXACT))
    assert d13.allowlist_report(EXACT) == {
        "exact": True,
        "tools": EXACT,
        "unexpected": [],
        "missing": [],
    }


@pytest.mark.parametrize(
    "extra", ["accept_dispute_claim", "create_refund", "update_shipment_tracking", "list_invoices"]
)
def test_any_extra_tool_is_rejected_by_name(extra):
    with pytest.raises(ValueError, match=extra):
        d13.check_allowlist([*EXACT, extra])
    assert d13.allowlist_report([*EXACT, extra])["unexpected"] == [extra]


@pytest.mark.parametrize("missing", EXACT)
def test_any_missing_tool_is_rejected_by_name(missing):
    names = [name for name in EXACT if name != missing]
    with pytest.raises(ValueError, match=f"missing: {missing}"):
        d13.check_allowlist(names)


def test_duplicates_and_non_strings_are_rejected():
    with pytest.raises(ValueError, match="duplicated: get_dispute"):
        d13.check_allowlist([*EXACT, "get_dispute"])
    with pytest.raises(ValueError, match="strings"):
        d13.check_allowlist([*EXACT[:-1], None])
    with pytest.raises(ValueError, match="missing"):
        d13.check_allowlist([])


def test_allowed_actions_are_read_only():
    assert "create" not in d13.ALLOWED_ACTIONS["disputes"]
    for product, verbs in d13.ALLOWED_ACTIONS.items():
        for verb, enabled in verbs.items():
            assert verb in {"list", "get"}, f"{product}.{verb}"
            assert enabled is True
            assert not any(word in verb.lower() for word in ("create", "update", "accept"))
    d13.check_actions(d13.ALLOWED_ACTIONS)


@pytest.mark.parametrize(
    "actions",
    [
        {"disputes": {"list": True, "get": True, "create": True}},
        {"shipment": {"update": True}},
        {"orders": {"capture": True}},
        {"disputes": {"accept": True}},
        {"invoices": {"recordRefund": True}},
        {"disputes": {"list": False}},
    ],
)
def test_check_actions_rejects_anything_but_enabled_list_or_get(actions):
    with pytest.raises(ValueError, match="list or get only"):
        d13.check_actions(actions)


def test_allowed_actions_select_exactly_the_allowlist_in_toolkit_1_11():
    # Same rule as the toolkit's is_tool_allowed: ANY enabled action enables the tool.
    selected = {
        method
        for method, actions in TOOLKIT_1_11_TOOLS.items()
        if any(
            d13.ALLOWED_ACTIONS.get(product, {}).get(verb, False)
            for product, verbs in actions.items()
            for verb in verbs
        )
    }
    assert selected == d13.ALLOWED_TOOLS


# --- the real toolkit, when installed (no network: sockets are disabled) ---


@pytest.fixture
def no_network(monkeypatch):
    def deny(*_args, **_kwargs):
        raise AssertionError("the toolkit attempted a network call")

    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr(socket, "getaddrinfo", deny)


def real_toolkit():
    return pytest.importorskip(
        "paypal_agent_toolkit.langchain.toolkit",
        reason="paypal-agent-toolkit and langchain are not installed here; D13 installs them "
        "in a throwaway venv instead",
    )


def build(actions):
    toolkit_module = real_toolkit()
    config = pytest.importorskip("paypal_agent_toolkit.shared.configuration")
    configuration = config.Configuration(actions=actions, context=config.Context(sandbox=True))
    return toolkit_module.PayPalToolkit(
        client_id=CLIENT_ID, secret=SECRET, configuration=configuration
    )


@pytest.mark.usefixtures("no_network")
def test_real_toolkit_exposes_exactly_the_allowlist():
    toolkit = build(d13.ALLOWED_ACTIONS)
    d13.check_allowlist(tool.name for tool in toolkit.get_tools())
    assert toolkit.get_paypal_api()._paypal_client.base_url == d13.SANDBOX_BASE_URL


@pytest.mark.usefixtures("no_network")
def test_real_toolkit_disputes_create_would_add_accept_dispute_claim():
    actions = {**d13.ALLOWED_ACTIONS, "disputes": {"list": True, "get": True, "create": True}}
    names = [tool.name for tool in build(actions).get_tools()]
    assert "accept_dispute_claim" in names
    with pytest.raises(ValueError, match="accept_dispute_claim"):
        d13.check_allowlist(names)


def run_real_child(tmp_path, plan, env=None):
    real_toolkit()
    child = tmp_path / d13.CHILD_NAME
    child.write_text(d13.CHILD_SCRIPT, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-I", str(child)],
        input=json.dumps(plan).encode(),
        capture_output=True,
        env={**d13.child_env(), **(env or {})},
        timeout=300,
        check=False,
    )
    return json.loads(result.stdout)


def test_real_child_lists_exact_tools_without_network(tmp_path):
    listing = run_real_child(tmp_path, {"mode": "list", "actions": d13.ALLOWED_ACTIONS})
    d13.check_allowlist(listing["tools"])
    assert listing["network_attempts"] == 0
    assert listing["base_url"] == d13.SANDBOX_BASE_URL


def test_real_child_refuses_to_call_when_the_set_differs(tmp_path):
    plan = {
        "mode": "call",
        "actions": {**d13.ALLOWED_ACTIONS, "disputes": {"list": True, "create": True}},
        "expected": EXACT,
        "calls": [{"tool": "accept_dispute_claim", "args": {"dispute_id": "PP-D-1", "note": "x"}}],
    }
    env = {d13.CLIENT_ID_ENV: CLIENT_ID, d13.SECRET_ENV: SECRET}
    result = run_real_child(tmp_path, plan, env)
    assert result["refused"] == "allowlist"
    assert "calls" not in result


# --- the D13 steps with a faked subprocess runner ---


class FakeProcesses:
    """Plays venv creation, pip and the child; records every invocation."""

    def __init__(self, *, tools=EXACT, fail=None, child_error=None):
        self.tools = list(tools)
        self.fail = fail  # "venv", "toolkit", "adapter" or "venv_oserror"
        self.child_error = child_error  # raised on the first list call, for resume tests
        self.invocations: list[dict] = []
        self.call_plans: list[dict] = []

    def count(self, kind):
        return sum(1 for item in self.invocations if item["kind"] == kind)

    async def __call__(self, argv, *, env, stdin=None, limit):
        assert limit > 0
        kind = self.kind(argv)
        plan = json.loads(stdin) if stdin else None
        self.invocations.append({"kind": kind, "argv": list(argv), "env": dict(env), "plan": plan})
        if kind == "venv":
            if self.fail == "venv_oserror":
                raise FileNotFoundError(f"cannot run {SECRET}")
            python = d13.venv_python(d13.Path(argv[-1]))
            python.parent.mkdir(parents=True, exist_ok=True)
            python.write_text("", encoding="utf-8")
            return d13.ProcessResult(1 if self.fail == "venv" else 0, b"", b"")
        if kind in {"toolkit", "adapter"}:
            failed = self.fail == kind
            return d13.ProcessResult(
                1 if failed else 0, b"Successfully installed", f"error {SECRET}".encode()
            )
        if kind == "versions":
            packages = [
                {"name": "paypal_agent_toolkit", "version": "1.11.0"},
                {"name": "langchain", "version": "0.3.23"},
                {"name": "unrelated", "version": "9"},
            ]
            return d13.ProcessResult(0, json.dumps(packages).encode(), b"")
        if plan["mode"] == "list":
            if self.child_error and self.count("child") == 1:
                raise self.child_error
            body = {
                "tools": self.tools,
                "tool_classes": ["paypal_agent_toolkit.langchain.tool.PayPalTool"],
                "base_url": d13.SANDBOX_BASE_URL,
                "network_attempts": 0,
            }
            return d13.ProcessResult(0, json.dumps(body).encode(), b"")
        self.call_plans.append(plan)
        calls = [
            {
                "tool": call["tool"],
                "ok": True,
                "output": {"echo": call["args"], "note": f"{BUYER} {SECRET} {CLIENT_ID}"},
            }
            for call in plan["calls"]
        ]
        body = {"tools": self.tools, "base_url": d13.SANDBOX_BASE_URL, "calls": calls}
        return d13.ProcessResult(0, json.dumps(body).encode(), f"warn {SECRET}".encode())

    @staticmethod
    def kind(argv):
        if argv[1:3] == ["-m", "venv"]:
            return "venv"
        if "--no-deps" in argv:
            return "toolkit"
        if "install" in argv:
            return "adapter"
        if "list" in argv:
            return "versions"
        return "child"


def handle(request):
    if request.url.path == "/v1/oauth2/token":
        return httpx.Response(200, json={"access_token": TOKEN, "expires_in": 3600, "scope": ""})
    assert request.url.path == "/v1/reporting/balances", request.url.path
    return httpx.Response(200, json={"balances": []})


def seed(root, check_id, data):
    path = root / "2026-10-24" / check_id / "state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    state = {"schema_version": 1, "check_id": check_id, "completed_steps": [], "data": data}
    path.write_text(json.dumps(state), encoding="utf-8")


@pytest.fixture
def root(tmp_path, monkeypatch):
    temp = tmp_path / "system-temp"
    temp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp))
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", SECRET)  # as if load_settings had read .env
    monkeypatch.setenv("SBX_BUYER_EMAIL", BUYER)
    monkeypatch.setenv("LANGSMITH_API_KEY", "FAKE-LANGSMITH-KEY")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    records = tmp_path / "recordings"
    seed(records, "D1", {"verdict": "CONFIRMED"})
    seed(
        records,
        "D2",
        {
            "captures": {"inr_400": {"capture_id": "CAP4000000001"}, "snad_60": "CAP600000001"},
            "orders": {"snad_60": {"order_id": ORDER_ID}},
        },
    )
    seed(records, "D8", {"disputes": {"inr_400": {"dispute_id": "PP-D-0001"}}})
    return records


@pytest.fixture
def lines():
    return []


def runner(root, lines):
    settings = SandboxSettings(client_id=CLIENT_ID, client_secret=SECRET, buyer_email=BUYER)
    return Runner(
        settings,
        root=root,
        transport=httpx.MockTransport(handle),
        clock=lambda: NOW,
        output=lines.append,
        manual=ManualSteps(Redactor.from_settings(settings), output_fn=lines.append),
    )


async def run_d13(root, lines, fake):
    return await runner(root, lines).run("D13", steps=d13.get_steps(fake))


def findings(root):
    return json.loads((root / "2026-10-24" / "D13" / "findings.json").read_text(encoding="utf-8"))


def assert_no_private_values(root, lines):
    text = "".join(p.read_text(encoding="utf-8") for p in root.rglob("*") if p.is_file())
    text += "\n".join(lines)
    for private in (CLIENT_ID, SECRET, TOKEN, BUYER, str(tempfile.gettempdir())):
        assert private not in text


def test_registry_default_steps_use_the_real_process_runner():
    steps = d13.get_steps()
    assert [step.name for step in steps] == [
        "create_venv",
        "list_tools",
        "assert_allowlist",
        "call_tools",
        "findings",
    ]
    assert steps[0].run.keywords["run"] is d13.run_process


@pytest.mark.asyncio
async def test_exact_set_confirms_and_calls_each_read_tool_once(root, lines):
    fake = FakeProcesses()
    await run_d13(root, lines, fake)
    finding = findings(root)
    assert finding["verdict"] == "CONFIRMED"
    assert finding["versions"] == {"paypal-agent-toolkit": "1.11.0", "langchain": "0.3.23"}
    assert finding["listing"]["network_attempts_while_listing"] == 0
    assert finding["tool_calls"]["skipped"] == {}
    assert set(finding["tool_calls"]["results"]) == d13.ALLOWED_TOOLS

    [plan] = fake.call_plans
    args = {call["tool"]: call["args"] for call in plan["calls"]}
    assert args["get_dispute"] == {"dispute_id": "PP-D-0001"}
    assert args["get_order_details"] == {"order_id": ORDER_ID}
    assert args["get_shipment_tracking"] == {"transaction_id": "CAP4000000001"}
    assert args["list_transactions"]["start_date"] == "2026-09-24T09:00:00Z"
    assert args["list_transactions"]["end_date"] == "2026-10-24T09:00:00Z"
    assert plan["actions"] == d13.ALLOWED_ACTIONS
    assert plan["expected"] == EXACT

    install = fake.invocations[1]["argv"]
    assert install[-2:] == ["--no-deps", d13.TOOLKIT_REQUIREMENT]
    for item in fake.invocations:
        assert SECRET not in " ".join(item["argv"])
        assert CLIENT_ID not in " ".join(item["argv"])
        assert "PAYPAL_CLIENT_SECRET" not in item["env"]
        assert "SBX_BUYER_EMAIL" not in item["env"]
        assert "LANGSMITH_API_KEY" not in item["env"]
        assert item["env"]["LANGCHAIN_TRACING_V2"] == "false"
        calling = item["plan"] is not None and item["plan"]["mode"] == "call"
        assert (item["env"].get(d13.SECRET_ENV) == SECRET) is calling
        assert (item["env"].get(d13.CLIENT_ID_ENV) == CLIENT_ID) is calling

    check_dir = root / "2026-10-24" / "D13"
    for name in d13.ALLOWED_TOOLS:
        assert (check_dir / f"tool_{name}.json").is_file()
    assert not any(d13.Path(tempfile.gettempdir()).iterdir())  # throwaway venv removed
    assert_no_private_values(root, lines)


@pytest.mark.asyncio
async def test_extra_write_tool_is_different_and_nothing_is_called(root, lines):
    fake = FakeProcesses(tools=[*EXACT, "accept_dispute_claim"])
    await run_d13(root, lines, fake)
    finding = findings(root)
    assert finding["verdict"] == "DIFFERENT"
    assert finding["allowlist"]["unexpected"] == ["accept_dispute_claim"]
    assert finding["tool_calls"] is None
    assert fake.call_plans == []
    assert all(item["plan"] is None or item["plan"]["mode"] == "list" for item in fake.invocations)
    assert not any(d13.SECRET_ENV in item["env"] for item in fake.invocations)
    assert any("D13 STOP" in line for line in lines)
    assert_no_private_values(root, lines)


@pytest.mark.asyncio
async def test_missing_tool_is_different(root, lines):
    fake = FakeProcesses(tools=EXACT[1:])
    await run_d13(root, lines, fake)
    assert findings(root)["verdict"] == "DIFFERENT"
    assert findings(root)["allowlist"]["missing"] == [EXACT[0]]
    assert fake.call_plans == []


@pytest.mark.asyncio
@pytest.mark.parametrize("fail", ["venv", "toolkit", "adapter", "venv_oserror"])
async def test_install_failure_is_blocked(root, lines, fail):
    fake = FakeProcesses(fail=fail)
    await run_d13(root, lines, fake)
    finding = findings(root)
    assert finding["verdict"] == "BLOCKED"
    assert finding["listing"] is None
    assert fake.count("child") == 0
    assert_no_private_values(root, lines)


@pytest.mark.asyncio
async def test_missing_inputs_are_skipped_not_guessed(root, lines):
    seed(root, "D2", {})
    seed(root, "D8", {"disputes": {"inr_400": {"not_found": True}}})
    fake = FakeProcesses()
    await run_d13(root, lines, fake)
    finding = findings(root)
    assert finding["verdict"] == "CONFIRMED"
    assert set(finding["tool_calls"]["skipped"]) == {
        "get_dispute",
        "get_order_details",
        "get_shipment_tracking",
    }
    [plan] = fake.call_plans
    assert [call["tool"] for call in plan["calls"]] == ["list_disputes", "list_transactions"]


@pytest.mark.asyncio
async def test_call_time_refusal_is_different(root, lines):
    fake = FakeProcesses()
    original = fake.__call__

    async def refusing(argv, *, env, stdin=None, limit):
        result = await original(argv, env=env, stdin=stdin, limit=limit)
        if stdin and json.loads(stdin)["mode"] == "call":
            body = {"refused": "allowlist", "tools": [*EXACT, "accept_dispute_claim"]}
            return d13.ProcessResult(0, json.dumps(body).encode(), b"")
        return result

    await run_d13(root, lines, refusing)
    finding = findings(root)
    assert finding["verdict"] == "DIFFERENT"
    assert finding["tool_calls"]["ran"] is False


@pytest.mark.asyncio
async def test_resume_reuses_the_checkpointed_venv(root, lines):
    fake = FakeProcesses(child_error=RuntimeError(f"boom {SECRET}"))
    with pytest.raises(CheckFailed, match="list_tools") as error:
        await run_d13(root, lines, fake)
    assert SECRET not in str(error.value)
    state = json.loads((root / "2026-10-24" / "D13" / "state.json").read_text(encoding="utf-8"))
    assert state["completed_steps"] == ["create_venv"]
    assert state["data"]["temp_dir_name"].startswith(d13.TEMP_PREFIX)
    assert tempfile.gettempdir() not in json.dumps(state)

    await run_d13(root, lines, fake)
    assert findings(root)["verdict"] == "CONFIRMED"
    assert fake.count("venv") == 1  # not reinstalled on resume
    assert_no_private_values(root, lines)
