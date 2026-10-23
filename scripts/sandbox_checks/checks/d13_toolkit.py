"""D13: the PayPal Agent Toolkit, configured for CounterSign, exposes only read tools (hard rule).

paypal-agent-toolkit==1.11.0 is installed into a throwaway venv outside the repo. A child
process builds the LangChain adapter (paypal_agent_toolkit.langchain.toolkit.PayPalToolkit)
with ALLOWED_ACTIONS and reports its tool names; nothing is called unless the set is exact.

Findings from the 1.11.0 source (shared/configuration.py, shared/tools.py, langchain/):
- A tool is enabled when ANY of its action keys is true, and disputes.create enables
  accept_dispute_claim; so actions are checked here as well as the resulting tool names.
- Context(sandbox=True) is required: Context() defaults to the live API, and omitting the
  context raises AttributeError in the adapter.
- Building the toolkit and listing tools make no network call; each tool call fetches a
  fresh OAuth token with requests.
- The wheel requires crewai-tools and openai-agents, whose dependencies do not build on
  Windows; the LangChain adapter imports only langchain, requests and pydantic, so the
  toolkit is installed without dependencies plus ADAPTER_REQUIREMENTS.

Tool inputs come from earlier checks: D8 data["disputes"][role]["dispute_id"]; D2
data["captures"][role] (string or {"capture_id", "order_id"}) and data["orders"][role]
(string or {"order_id"}). A tool whose input is missing is recorded as skipped.
"""

import asyncio
import json
import os
import re
import shutil
import sys
import tempfile
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import timedelta
from functools import partial
from pathlib import Path

from ..recorder import timestamp
from ..runner import CheckContext, Step
from .d08_dispute_create import RESOURCE_ID, ROLES, d2_capture, now

TOOLKIT_REQUIREMENT = "paypal-agent-toolkit==1.11.0"
ADAPTER = "paypal_agent_toolkit.langchain.toolkit.PayPalToolkit"
ADAPTER_REQUIREMENTS = ("langchain==0.3.23", "requests>=2.31.0", "pydantic>=2.10")
VERSION_PACKAGES = ("paypal-agent-toolkit", "langchain", "langchain-core", "pydantic", "requests")
ALLOWED_ACTIONS: dict[str, dict[str, bool]] = {
    "disputes": {"list": True, "get": True},
    "shipment": {"get": True},
    "orders": {"get": True},
    "transactions": {"list": True},
}
ALLOWED_TOOLS = frozenset(
    {
        "list_disputes",
        "get_dispute",
        "get_shipment_tracking",
        "get_order_details",
        "list_transactions",
    }
)
READ_VERBS = frozenset({"list", "get"})
SANDBOX_BASE_URL = "https://api-m.sandbox.paypal.com"
TEMP_PREFIX = "countersign-d13-"
CHILD_NAME = "d13_child.py"
CLIENT_ID_ENV = "COUNTERSIGN_D13_CLIENT_ID"
SECRET_ENV = "COUNTERSIGN_D13_CLIENT_SECRET"
# Credentials and LangSmith tracing settings (langchain would upload tool traces) stay out.
PRIVATE_ENV = re.compile(
    r"(?i)^(PAYPAL_|SBX_|COUNTERSIGN_D13_|LANGCHAIN_|LANGSMITH_|DATABASE_URL$)"
)
ORDER_ID = re.compile(r"[A-Z0-9]{17,32}")  # the toolkit's ORDER_ID_REGEX
INSTALL_TIMEOUT = 900.0
CHILD_TIMEOUT = 300.0
LOG_TAIL = 4000
TRANSACTION_WINDOW = timedelta(days=30)  # the reporting API allows at most 31 days

CHILD_SCRIPT = r'''"""CounterSign D13 child: build the LangChain toolkit; list or call its tools.

Reads a JSON plan on stdin and writes one JSON object to stdout. Credentials arrive only in
environment variables and are never printed. Errors are reported by type name only.
"""
import contextlib
import json
import logging
import os
import re
import socket
import sys

SANDBOX = "https://api-m.sandbox.paypal.com"
STATUS = re.compile(r"\b([1-5][0-9]{2}) (?:Client|Server) Error")
TOOLKIT_ERROR = "Error executing PayPalTool"


def build(actions, client_id, secret):
    from paypal_agent_toolkit.langchain.toolkit import PayPalToolkit
    from paypal_agent_toolkit.shared.configuration import Configuration, Context

    configuration = Configuration(actions=actions, context=Context(sandbox=True))
    toolkit = PayPalToolkit(client_id=client_id, secret=secret, configuration=configuration)
    return toolkit, toolkit.get_paypal_api()._paypal_client.base_url


def invoke(tool, args):
    try:
        text = tool.invoke(args)
    except Exception as error:
        return {"tool": tool.name, "ok": False, "error": type(error).__name__}
    if isinstance(text, str) and text.startswith(TOOLKIT_ERROR):
        # The adapter flattens exceptions into text; keep only the HTTP status.
        match = STATUS.search(text)
        return {
            "tool": tool.name,
            "ok": False,
            "error": "ToolkitError",
            "http_status": int(match.group(1)) if match else None,
        }
    try:
        output = json.loads(text) if isinstance(text, str) else text
    except ValueError:
        output = {"text": text}
    return {"tool": tool.name, "ok": True, "output": output}


def list_mode(plan):
    attempts = []

    def deny(*_args, **_kwargs):
        attempts.append(1)
        raise ConnectionRefusedError("network is disabled while listing tools")

    socket.socket.connect = deny
    socket.create_connection = deny
    socket.getaddrinfo = deny
    toolkit, base_url = build(plan["actions"], "d13-list-only", "d13-list-only")
    tools = toolkit.get_tools()
    return {
        "tools": [tool.name for tool in tools],
        "tool_classes": sorted({f"{type(t).__module__}.{type(t).__qualname__}" for t in tools}),
        "base_url": base_url,
        "network_attempts": len(attempts),
    }


def call_mode(plan):
    client_id = os.environ.pop("COUNTERSIGN_D13_CLIENT_ID", "")
    secret = os.environ.pop("COUNTERSIGN_D13_CLIENT_SECRET", "")
    toolkit, base_url = build(plan["actions"], client_id, secret)
    tools = toolkit.get_tools()
    names = [tool.name for tool in tools]
    if base_url != SANDBOX:
        return {"refused": "not_sandbox", "tools": names}
    if len(names) != len(set(names)) or set(names) != set(plan["expected"]):
        return {"refused": "allowlist", "tools": names}
    by_name = {tool.name: tool for tool in tools}
    calls = []
    for call in plan["calls"]:
        if call["tool"] not in by_name:
            calls.append({"tool": call["tool"], "ok": False, "error": "NotAllowed"})
            continue
        calls.append(invoke(by_name[call["tool"]], call["args"]))
    return {"tools": names, "base_url": base_url, "calls": calls}


def main():
    logging.disable(logging.CRITICAL)
    stdout = sys.stdout
    try:
        plan = json.loads(sys.stdin.read())
        with contextlib.redirect_stdout(sys.stderr):
            result = list_mode(plan) if plan["mode"] == "list" else call_mode(plan)
    except Exception as error:
        stdout.write(json.dumps({"error": type(error).__name__}))
        return 3
    stdout.write(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''


@dataclass(frozen=True)
class ProcessResult:
    returncode: int
    stdout: bytes
    stderr: bytes


ProcessRunner = Callable[..., Awaitable[ProcessResult]]


async def run_process(
    argv: list[str], *, env: Mapping[str, str], stdin: bytes | None = None, limit: float
) -> ProcessResult:
    """Run argv without a shell; kill it after limit seconds."""
    process = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=dict(env),
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(stdin), limit)
    except TimeoutError:
        process.kill()
        await process.wait()
        raise
    return ProcessResult(process.returncode or 0, stdout, stderr)


def check_actions(actions: Mapping[str, Mapping[str, bool]]) -> None:
    """Raise ValueError unless every configured action is a read verb set to True."""
    problems = sorted(
        f"{product}.{verb}"
        for product, verbs in actions.items()
        for verb, enabled in verbs.items()
        if verb not in READ_VERBS or enabled is not True
    )
    if problems:
        raise ValueError("Toolkit actions must be list or get only; found " + ", ".join(problems))


def check_allowlist(tool_names) -> None:
    """Raise ValueError unless tool_names is exactly ALLOWED_TOOLS, each once."""
    names = list(tool_names)
    if not all(isinstance(name, str) for name in names):
        raise ValueError("Tool names must be strings.")
    unexpected = sorted(set(names) - ALLOWED_TOOLS)
    missing = sorted(ALLOWED_TOOLS - set(names))
    duplicates = sorted({name for name in names if names.count(name) > 1})
    problems = [
        f"{label}: {', '.join(values)}"
        for label, values in (
            ("unexpected", unexpected),
            ("missing", missing),
            ("duplicated", duplicates),
        )
        if values
    ]
    if problems:
        raise ValueError("Toolkit tool set differs from the allowlist; " + "; ".join(problems))


def allowlist_report(tool_names) -> dict:
    names = list(tool_names)
    try:
        check_allowlist(names)
    except ValueError:
        exact = False
    else:
        exact = True
    return {
        "exact": exact,
        "tools": sorted(names),
        "unexpected": sorted(set(names) - ALLOWED_TOOLS),
        "missing": sorted(ALLOWED_TOOLS - set(names)),
    }


def venv_python(venv_dir: Path) -> Path:
    return venv_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def child_env(credentials: Mapping[str, str] | None = None) -> dict[str, str]:
    """The parent environment minus anything private, plus credentials only when calling."""
    env = {key: value for key, value in os.environ.items() if not PRIVATE_ENV.match(key)}
    env |= {
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        "PIP_NO_INPUT": "1",
        "LANGCHAIN_TRACING_V2": "false",
        "LANGSMITH_TRACING": "false",
    }
    if credentials:
        env |= credentials
    return env


def public_log(ctx: CheckContext, data: bytes, temp_dir: Path | None = None) -> str:
    text = data.decode("utf-8", errors="replace")
    for private, label in ((temp_dir, "<d13-temp>"), (Path.home(), "<home>")):
        if private is not None:
            text = text.replace(str(private), label)
    return ctx.recorder.redactor.text(text[-LOG_TAIL:])


def temp_path(ctx: CheckContext) -> Path | None:
    """The checkpointed temp dir; only its name is saved, so no user path enters state.json."""
    name = ctx.data.get("temp_dir_name")
    if not isinstance(name, str) or not re.fullmatch(re.escape(TEMP_PREFIX) + r"[\w-]+", name):
        return None
    return Path(tempfile.gettempdir()) / name


async def run_logged(
    ctx: CheckContext, run: ProcessRunner, label: str, argv: list[str], **kwargs
) -> ProcessResult | None:
    """Run a process; save a redacted log and return None on any failure."""
    temp_dir = temp_path(ctx)
    try:
        result = await run(argv, **kwargs)
    except (OSError, TimeoutError) as error:
        ctx.write_json(ctx.check_dir / f"{label}.json", {"error": type(error).__name__})
        return None
    ctx.write_json(
        ctx.check_dir / f"{label}.json",
        {
            "returncode": result.returncode,
            "stdout_tail": public_log(ctx, result.stdout, temp_dir),
            "stderr_tail": public_log(ctx, result.stderr, temp_dir),
        },
    )
    return result if result.returncode == 0 else None


async def ensure_venv(ctx: CheckContext, run: ProcessRunner) -> dict:
    """Create or reuse the throwaway venv; return install details, or a BLOCKED reason."""
    temp_dir = temp_path(ctx)
    if temp_dir is None or not temp_dir.is_dir():
        temp_dir = Path(tempfile.mkdtemp(prefix=TEMP_PREFIX))
        ctx.checkpoint(temp_dir_name=temp_dir.name, venv_ready=False)
    venv_dir = temp_dir / "venv"
    python = venv_python(venv_dir)
    if ctx.data.get("venv_ready") and python.is_file():
        return {"installed": True}
    ctx.checkpoint(venv_ready=False)
    env = child_env()
    pip = [str(python), "-m", "pip", "--disable-pip-version-check", "--no-input"]
    commands = (
        ("install_venv", [sys.executable, "-m", "venv", str(venv_dir)]),
        ("install_toolkit", [*pip, "install", "--no-deps", TOOLKIT_REQUIREMENT]),
        ("install_adapter", [*pip, "install", *ADAPTER_REQUIREMENTS]),
    )
    for label, argv in commands:
        if await run_logged(ctx, run, label, argv, env=env, limit=INSTALL_TIMEOUT) is None:
            return {"installed": False, "blocked_reason": f"{label} failed"}
    listing = await run_logged(
        ctx, run, "install_versions", [*pip, "list", "--format", "json"], env=env, limit=120.0
    )
    try:
        packages = json.loads(listing.stdout) if listing else []
        versions = {
            name: entry["version"]
            for entry in packages
            if (name := entry["name"].lower().replace("_", "-")) in VERSION_PACKAGES
        }
    except (ValueError, TypeError, KeyError):
        versions = {}
    if versions.get("paypal-agent-toolkit") != TOOLKIT_REQUIREMENT.split("==")[1]:
        return {"installed": False, "blocked_reason": "pinned toolkit version not installed"}
    ctx.checkpoint(venv_ready=True, versions=versions)
    return {"installed": True}


async def run_child(ctx: CheckContext, run: ProcessRunner, label: str, plan: dict, *, env) -> dict:
    """Run the child in the venv; return its parsed JSON, or {"error": type name}."""
    temp_dir = temp_path(ctx)
    child = temp_dir / CHILD_NAME
    child.write_text(CHILD_SCRIPT, encoding="utf-8")
    python = venv_python(temp_dir / "venv")
    try:
        result = await run(
            [str(python), "-I", str(child)],
            env=env,
            stdin=json.dumps(plan).encode(),
            limit=CHILD_TIMEOUT,
        )
    except (OSError, TimeoutError) as error:
        return {"error": type(error).__name__}
    ctx.write_json(
        ctx.check_dir / f"{label}_stderr.json",
        {"returncode": result.returncode, "stderr_tail": public_log(ctx, result.stderr, temp_dir)},
    )
    try:
        parsed = json.loads(result.stdout)
    except ValueError:
        return {"error": "InvalidChildOutput"}
    if not isinstance(parsed, dict):
        return {"error": "InvalidChildOutput"}
    if result.returncode != 0 and "error" not in parsed:
        return {"error": "ChildFailed"}
    return ctx.recorder.redactor.redact(parsed)


async def create_venv(ctx: CheckContext, *, run: ProcessRunner) -> dict:
    check_actions(ALLOWED_ACTIONS)
    install = await ensure_venv(ctx, run)
    ctx.note(f"D13 install: {'ok' if install['installed'] else install['blocked_reason']}")
    return {"install": install, "versions": ctx.data.get("versions", {})}


async def list_tools(ctx: CheckContext, *, run: ProcessRunner) -> dict | None:
    if not ctx.data["install"]["installed"]:
        return None
    install = await ensure_venv(ctx, run)  # the temp venv may be gone after a long pause
    if not install["installed"]:
        return {"install": install}
    plan = {"mode": "list", "actions": ALLOWED_ACTIONS}
    listing = await run_child(ctx, run, "list_tools", plan, env=child_env())
    ctx.write_json(ctx.check_dir / "list_tools.json", listing)
    names = listing.get("tools")
    if "error" in listing or not isinstance(names, list):
        error = listing.get("error", "InvalidChildOutput")
        ctx.note(f"D13 tool listing failed ({error}).")
        return {"listing": {"ok": False, "error": error}}
    ctx.note("D13 toolkit tools: " + ", ".join(sorted(map(str, names))))
    return {
        "listing": {
            "ok": True,
            "tools": names,
            "tool_classes": listing.get("tool_classes"),
            "sandbox_base_url": listing.get("base_url") == SANDBOX_BASE_URL,
            "network_attempts_while_listing": listing.get("network_attempts"),
        }
    }


async def assert_allowlist(ctx: CheckContext) -> dict | None:
    listing = ctx.data.get("listing") or {}
    if not listing.get("ok"):
        return None
    report = allowlist_report(listing["tools"])
    if not report["exact"]:
        ctx.note(
            "D13 STOP: the toolkit tool set differs from the allowlist "
            f"(unexpected: {report['unexpected']}, missing: {report['missing']}). "
            "No tool will be called."
        )
    return {"allowlist": report}


async def call_tools(ctx: CheckContext, *, run: ProcessRunner) -> dict | None:
    if not (ctx.data.get("allowlist") or {}).get("exact"):
        return None
    calls, skipped = tool_calls(ctx)
    plan = {
        "mode": "call",
        "actions": ALLOWED_ACTIONS,
        "expected": sorted(ALLOWED_TOOLS),
        "calls": calls,
    }
    settings = ctx.client.settings
    env = child_env({CLIENT_ID_ENV: settings.client_id, SECRET_ENV: settings.client_secret})
    result = await run_child(ctx, run, "call_tools", plan, env=env)
    ctx.write_json(ctx.check_dir / "call_tools.json", {**result, "skipped": skipped})
    if "error" in result or "refused" in result or not isinstance(result.get("calls"), list):
        reason = result.get("refused") or result.get("error") or "InvalidChildOutput"
        ctx.note(f"D13 tool calls did not run ({reason}).")
        return {"tool_calls": {"ran": False, "reason": reason, "skipped": skipped}}
    summary = {}
    for entry in result["calls"]:
        name = entry.get("tool") if isinstance(entry, dict) else None
        if name in ALLOWED_TOOLS:
            ctx.write_json(ctx.check_dir / f"tool_{name}.json", entry)
            summary[name] = {
                key: entry[key] for key in ("ok", "error", "http_status") if key in entry
            }
    for name, reason in skipped.items():
        ctx.note(f"D13 {name}: skipped ({reason}).")
    return {"tool_calls": {"ran": True, "results": summary, "skipped": skipped}}


def verdict_for(data: dict) -> tuple[str, str]:
    install = data.get("install") or {}
    listing = data.get("listing") or {}
    if not install.get("installed"):
        return "BLOCKED", install.get("blocked_reason", "installation failed")
    if not listing.get("ok"):
        return "BLOCKED", f"tool listing failed ({listing.get('error')})"
    if not (data.get("allowlist") or {}).get("exact"):
        return "DIFFERENT", "tool set differs from the allowlist; nothing was called"
    refused = (data.get("tool_calls") or {}).get("reason")
    if refused in {"allowlist", "not_sandbox"}:
        return "DIFFERENT", f"the calling child refused ({refused}); nothing was called"
    return "CONFIRMED", "tool set equals the allowlist"


async def findings(ctx: CheckContext) -> dict:
    verdict, reason = verdict_for(ctx.data)
    finding = {
        "check_id": "D13",
        "verdict": verdict,
        "reason": reason,
        "toolkit": TOOLKIT_REQUIREMENT,
        "adapter": ADAPTER,
        "actions": ALLOWED_ACTIONS,
        "allowed_tools": sorted(ALLOWED_TOOLS),
        "versions": ctx.data.get("versions", {}),
        "listing": ctx.data.get("listing"),
        "allowlist": ctx.data.get("allowlist"),
        "tool_calls": ctx.data.get("tool_calls"),
        "observed_at": ctx.state["started_at"],
        "completed_at": now(ctx),
        "evidence_files": sorted(
            path.relative_to(ctx.root).as_posix()
            for path in ctx.check_dir.glob("*.json")
            if path.name not in {"state.json", "findings.json"}
        ),
    }
    ctx.write_json(ctx.check_dir / "findings.json", finding)
    ctx.note(f"D13: {verdict}")
    remove_temp(ctx)
    return {"verdict": verdict}


def remove_temp(ctx: CheckContext) -> None:
    """Delete the throwaway venv; temp_path only accepts a countersign-d13-* temp dir name."""
    temp_dir = temp_path(ctx)
    if temp_dir is not None:
        shutil.rmtree(temp_dir, ignore_errors=True)


def saved_id(entry, key: str, pattern: re.Pattern[str]) -> str | None:
    """An ID saved as a plain string or under key in a dict, if it matches pattern."""
    value = entry.get(key) if isinstance(entry, dict) else entry
    return value if isinstance(value, str) and pattern.fullmatch(value) else None


def first_saved_id(entries: list, key: str, pattern: re.Pattern[str]) -> str | None:
    return next((value for entry in entries if (value := saved_id(entry, key, pattern))), None)


def tool_calls(ctx: CheckContext) -> tuple[list[dict], dict[str, str]]:
    """Safe read-only arguments for each tool, from earlier checks' saved data."""
    d2 = ctx.load_check_data("D2")
    d8 = ctx.load_check_data("D8")
    calls = [{"tool": "list_disputes", "args": {"page_size": 10}}]
    skipped: dict[str, str] = {}

    disputes = d8.get("disputes") or {}
    dispute_id = first_saved_id([disputes.get(role) for role in ROLES], "dispute_id", RESOURCE_ID)
    if dispute_id:
        calls.append({"tool": "get_dispute", "args": {"dispute_id": dispute_id}})
    else:
        skipped["get_dispute"] = "D8 saved no dispute ID"

    orders = d2.get("orders") or {}
    captures = d2.get("captures") or {}
    order_id = first_saved_id(
        [orders.get(role) for role in ROLES], "order_id", ORDER_ID
    ) or first_saved_id(
        [entry for role in ROLES if isinstance(entry := captures.get(role), dict)],
        "order_id",
        ORDER_ID,
    )
    if order_id:
        calls.append({"tool": "get_order_details", "args": {"order_id": order_id}})
    else:
        skipped["get_order_details"] = "D2 saved no order ID"

    # Tracking was added to the $400 order. Pass the capture ID: in 1.11.0 the order_id
    # path calls a PayPalClient.get_order_details method that does not exist.
    capture_id = d2_capture(d2, "inr_400")
    if capture_id:
        calls.append({"tool": "get_shipment_tracking", "args": {"transaction_id": capture_id}})
    else:
        skipped["get_shipment_tracking"] = "D2 saved no capture ID for the $400 order"

    end = ctx.clock().replace(microsecond=0)
    calls.append(
        {
            "tool": "list_transactions",
            "args": {
                "start_date": timestamp(end - TRANSACTION_WINDOW),
                "end_date": timestamp(end),
                "page_size": 20,
            },
        }
    )
    return calls, skipped


def get_steps(run: ProcessRunner = run_process) -> list[Step]:
    """run is injectable so tests can fake the venv and child processes."""
    return [
        Step("create_venv", partial(create_venv, run=run)),
        Step("list_tools", partial(list_tools, run=run)),
        Step("assert_allowlist", assert_allowlist),
        Step("call_tools", partial(call_tools, run=run)),
        Step("findings", findings),
    ]
