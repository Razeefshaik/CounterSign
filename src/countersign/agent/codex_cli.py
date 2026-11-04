"""Local CLI chat boundary. No auth-file access, native tools or automatic retries."""

import asyncio
import copy
import json
import os
import shutil
import subprocess
import tempfile
import threading
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace

from countersign.agent.codex_cli_schema import decode_calls, strict_parameters
from countersign.agent.models import ModelError
from countersign.agent.tools import ALLOWED_TOOLS

CLI_VERSION = "codex-cli 0.160.0"
LIMIT = 1024 * 1024
ITEM_TYPES = frozenset({"agent_message", "reasoning", "error", "command_execution",
    "file_change", "web_search", "mcp_tool_call", "dynamic_tool_call", "collab_tool_call",
    "image_generation", "plan", "todo_list"})
DISABLED = (
    "shell_tool", "shell_snapshot", "shell_snapshot_v2", "apply_patch_freeform",
    "apps", "plugins", "remote_plugin", "enable_mcp_apps",
    "browser_use", "browser_use_external", "computer_use", "in_app_browser",
    "image_generation", "view_image", "js_repl", "js_repl_tools_only",
    "code_mode", "code_mode_host", "code_mode_only", "multi_agent", "multi_agent_v2",
    "goals", "memories", "search_tool", "tool_search",
    "tool_suggest", "exec_permission_approvals", "request_permissions_tool", "request_rule",
    "default_mode_request_user_input", "send_message_to_user_async", "sleep_tool",
    "current_time_reminder", "token_budget", "deferred_executor", "remote_control",
    "hooks", "plugin_hooks", "skill_mcp_dependency_install",
    "skill_search", "skill_env_var_dependency_prompt", "workspace_dependencies",
    "standalone_web_search",
)


def child_environment(source=None):
    source = os.environ if source is None else source
    names = {"SYSTEMROOT", "WINDIR", "USERPROFILE", "LOCALAPPDATA", "APPDATA", "TEMP", "TMP"}
    env = {name: value for name, value in source.items() if name.upper() in names}
    env["CODEX_HOME"] = source.get("CODEX_HOME", str(Path.home() / ".codex"))
    return env


def schema_for(tools):
    calls = []
    for tool in tools:
        function = tool["function"]
        if function["name"] not in ALLOWED_TOOLS:
            raise ModelError("MODEL_TOOL_NOT_ALLOWED")
        calls.append({"type": "object", "additionalProperties": False,
            "properties": {"id": {"type": "string"}, "name": {"type": "string", "enum": [function["name"]]},
                           "args": strict_parameters(function["parameters"])}, "required": ["id", "name", "args"]})
    # Structured text represents application calls. Native CLI function tools stay absent.
    return {"type": "object", "additionalProperties": False,
        "properties": {"tool_calls": {"type": "array", "maxItems": 4 if calls else 0,
            "items": {"anyOf": calls} if calls else {"type": "object", "additionalProperties": False,
                                                    "properties": {}, "required": []}},
            "proposal_json": {"type": "string"}}, "required": ["tool_calls", "proposal_json"]}


def isolated_catalog(original, model):
    catalog = copy.deepcopy(original)
    selected = [m for m in catalog["models"] if m["slug"] == model]
    if len(selected) != 1:
        raise ModelError("MODEL_CONFIGURATION_INVALID")
    selected[0].update(shell_type="disabled", apply_patch_tool_type=None,
                       experimental_supported_tools=[])
    catalog["models"] = selected
    return catalog


def command(binary, model, cwd, schema, catalog):
    args = [str(binary), "exec", "--ephemeral", "--ignore-user-config", "--ignore-rules",
        "--skip-git-repo-check", "--sandbox", "read-only", "--strict-config",
        "-C", str(cwd), "--model", model, "--json", "--color", "never",
        "--output-schema", str(schema)]
    overrides = ['web_search="disabled"', 'mcp_servers={}', 'plugins={}',
        'apps._default.enabled=false', 'tools.update_plan.enabled=false',
        'features.skip_host_skill_discovery=true', 'project_doc_max_bytes=0',
        'model_catalog_json=' + json.dumps(str(catalog))]
    overrides.extend(f"features.{name}=false" for name in DISABLED)
    for override in overrides:
        args.extend(["-c", override])
    return [*args, "-"]


class Events:
    """Allowlist JSON events; abort at item.started, before accepting any result."""

    def __init__(self):
        self.message = None
        self.usage = None
        self.types = []
        self.turn_started = False
        self.startup_warning_count = 0
        self.rejected_item_type = None

    def accept(self, event):
        kind = event.get("type")
        self.types.append(kind)
        if kind in {"thread.started", "turn.started"}:
            if kind == "turn.started":
                self.turn_started = True
            return
        if kind in {"item.started", "item.updated", "item.completed"}:
            item = event.get("item", {})
            item_type = item.get("type")
            if item_type == "error":
                # Startup diagnostics are not tools. Never retain their contents.
                if not self.turn_started:
                    self.startup_warning_count += 1
                    return
                self.rejected_item_type = "error"
                raise ModelError("MODEL_CLI_ERROR_ITEM")
            if not isinstance(item_type, str) or item_type not in {"agent_message", "reasoning"}:
                self.rejected_item_type = item_type if isinstance(item_type, str) and item_type in ITEM_TYPES else "unknown"
                raise ModelError("MODEL_NATIVE_TOOL_FORBIDDEN")
            if kind == "item.completed" and item["type"] == "agent_message":
                if self.message is not None:
                    raise ModelError("INVALID_MODEL_RESPONSE")
                self.message = item.get("text")
            return
        if kind == "turn.completed":
            self.usage = event.get("usage")
            return
        if kind in {"error", "turn.failed"}:
            error = event.get("error", {})
            code = error.get("code") if isinstance(error, dict) else None
            message = event.get("message", "")
            if isinstance(error, dict):
                message = error.get("message", message)
            # Inspect only to classify; never copy CLI error text into diagnostics.
            usage_stop = isinstance(message, str) and any(phrase in message.lower() for phrase in
                ("usage limit", "rate limit", "quota exceeded", "quota exhausted"))
            if usage_stop or code in {"usage_limit_reached", "rate_limit_exceeded", "insufficient_quota"}:
                raise ModelError("MODEL_QUOTA_EXHAUSTED")
            raise ModelError("MODEL_CALL_FAILED")
        raise ModelError("MODEL_EVENT_FORBIDDEN")


class CodexCLIClient:
    """LangChain-compatible bind_tools/ainvoke surface, one isolated exec per turn."""

    def __init__(self, *, binary, model, catalog, timeout=120, tools=(), proof=False):
        self.binary, self.model, self.catalog = Path(binary), model, catalog
        self.timeout, self.tools, self.proof = timeout, list(tools), proof
        self.last_events = []
        self.last_event_metadata = {}
        self.forbidden_output = None

    def __repr__(self):
        return "CodexCLIClient(values=<REDACTED>)"

    def bind_tools(self, tools):
        schema_for(tools)
        self.tools = copy.deepcopy(tools)
        return self

    def finish_step(self):
        self.tools = []

    async def aclose(self):
        self.finish_step()

    async def ainvoke(self, messages, config=None):
        if not self.proof:
            raise ModelError("MODEL_TOOL_DISABLE_PROOF_REQUIRED")
        serial = [message if isinstance(message, tuple) else message.model_dump(mode="json")
                  for message in messages]
        prompt = json.dumps({"conversation": serial, "application_read_tools": self.tools,
            "response_format": "Return tool_calls to request application reads, or proposal_json; "
                               "never invoke native CLI tools."})
        return await asyncio.to_thread(self.invoke, prompt)

    def invoke(self, prompt):
        events, total, stderr = Events(), 0, bytearray()
        self.last_event_metadata = {}
        with tempfile.TemporaryDirectory(prefix="c67-cli-") as folder:
            root = Path(folder)
            cwd = root / "empty"
            cwd.mkdir()
            schema, catalog = root / "output.json", root / "catalog.json"
            schema.write_text(json.dumps(schema_for(self.tools)), encoding="utf-8")
            catalog.write_text(json.dumps(isolated_catalog(self.catalog, self.model)), encoding="utf-8")
            proc = subprocess.Popen(command(self.binary, self.model, cwd, schema, catalog),
                cwd=cwd, env=child_environment(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            timer = threading.Timer(self.timeout, lambda: proc.kill() if proc.poll() is None else None)
            timer.start()

            def drain():
                for chunk in iter(lambda: proc.stderr.read(4096), b""):
                    if len(stderr) + len(chunk) > LIMIT:
                        proc.kill()
                        break
                    stderr.extend(chunk)

            reader = threading.Thread(target=drain, daemon=True)
            reader.start()
            try:
                proc.stdin.write(prompt.encode("utf-8"))
                proc.stdin.close()
                for raw in iter(lambda: proc.stdout.readline(LIMIT + 1), b""):
                    total += len(raw)
                    if total > LIMIT:
                        raise ModelError("INVALID_MODEL_RESPONSE")
                    if self.forbidden_output and self.forbidden_output.encode() in raw:
                        raise ModelError("MODEL_CANARY_DISCLOSED")
                    events.accept(json.loads(raw))
                proc.wait(timeout=5)
                reader.join(timeout=5)
                if self.forbidden_output and self.forbidden_output.encode() in stderr:
                    raise ModelError("MODEL_CANARY_DISCLOSED")
                if proc.returncode != 0 or events.message is None or events.usage is None:
                    raise ModelError("MODEL_CALL_FAILED")
                response = json.loads(events.message)
                if self.forbidden_output and self.forbidden_output in json.dumps(response):
                    raise ModelError("MODEL_CANARY_DISCLOSED")
                from jsonschema import validate

                validate(response, schema_for(self.tools))
                calls, proposal = decode_calls(response["tool_calls"], self.tools), response["proposal_json"]
                if bool(calls) == bool(proposal):
                    raise ModelError("INVALID_MODEL_RESPONSE")
                self.last_events = events.types
                return SimpleNamespace(tool_calls=calls, content=proposal,
                    usage_metadata=events.usage, invalid_tool_calls=[])
            except ModelError:
                proc.kill()
                raise
            except Exception:  # noqa: BLE001 - CLI/config/OS text can carry private data.
                proc.kill()
                raise ModelError("MODEL_CALL_FAILED") from None
            finally:
                self.last_event_metadata = {
                    "startup_warning_count": events.startup_warning_count,
                    "rejected_item_type": events.rejected_item_type,
                }
                timer.cancel()
                proc.wait(timeout=5)
                reader.join(timeout=5)
                proc.stdout.close()
                proc.stderr.close()


def find_binary():
    # Prefer the npm-distributed executable without involving a shell wrapper.
    if os.name == "nt":
        root = Path(os.environ.get("APPDATA", "")) / "npm/node_modules/@openai/codex"
        binary = root / "node_modules/@openai/codex-win32-x64/vendor/x86_64-pc-windows-msvc/bin/codex.exe"
        if binary.is_file():
            return binary
    binary = shutil.which("codex.exe" if os.name == "nt" else "codex")
    if not binary:
        raise ModelError("MODEL_DEPENDENCIES_REQUIRED")
    return Path(binary)


@lru_cache(maxsize=4)
def prepare(binary, model):
    # Metadata lookup uses a fresh CODEX_HOME and needs no login/auth files.
    with tempfile.TemporaryDirectory(prefix="c67-metadata-") as folder:
        env = {k: v for k, v in os.environ.items() if k.upper() in {"SYSTEMROOT", "WINDIR", "TEMP", "TMP"}}
        env["CODEX_HOME"] = folder
        def local(*args):
            return subprocess.run([str(binary), *args], check=False, cwd=folder, env=env,
                capture_output=True, encoding="utf-8", timeout=30,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        version = local("--version")
        if version.returncode or version.stdout.strip() != CLI_VERSION:
            raise ModelError("MODEL_CLI_VERSION_UNSUPPORTED")
        result = local("debug", "models", "--bundled")
        if result.returncode:
            raise ModelError("MODEL_CONFIGURATION_INVALID")
        catalog = json.loads(result.stdout)
    from countersign.agent.codex_cli_proof import prove

    # Inspect the actual wire tools; never rely only on config names or sandbox.
    proof = prove(binary, model, catalog)
    return catalog, proof


def create_client(config):
    if os.environ.get("COUNTERSIGN_HOSTED_START") or os.environ.get("COUNTERSIGN_HOSTED_STATE"):
        raise ModelError("MODEL_HOSTED_PROVIDER_REFUSED")
    binary = find_binary()
    catalog, proof = prepare(str(binary), config.model)
    return CodexCLIClient(binary=binary, model=config.model, catalog=catalog, proof=bool(proof))
