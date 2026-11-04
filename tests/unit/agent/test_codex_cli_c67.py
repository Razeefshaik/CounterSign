"""Stage-one boundary tests: invented events/metadata; no login or provider calls."""

import io
import json
from pathlib import Path

import pytest

from countersign.agent.codex_cli import (
    DISABLED,
    CodexCLIClient,
    Events,
    child_environment,
    command,
    isolated_catalog,
    schema_for,
)
from countersign.agent.models import ChatModelConfig, ModelError
from countersign.agent.runtime import load_chat_model_config
from countersign.config import SettingsError, load_hosted_settings


def test_keyless_provider_never_reads_any_key_entry():
    class Source(dict):
        def get(self, key, default=None):
            assert "KEY" not in key and "PAYPAL" not in key
            return super().get(key, default)
    config = load_chat_model_config(Source(COUNTERSIGN_LLM_PROVIDER="codex_cli",
                                          COUNTERSIGN_LLM_MODEL="local-test-model"))
    config.validate()
    assert config.provider == "codex_cli" and config.api_key is None
    with pytest.raises(ModelError):
        ChatModelConfig("codex_cli", "local-test-model", "invented-key").validate()


@pytest.mark.parametrize("hosted", [{"COUNTERSIGN_HOSTED_START": "friday"},
                                   {"COUNTERSIGN_HOSTED_STATE": "local-state"}])
def test_hosted_environment_refuses_before_cli_lookup(hosted):
    with pytest.raises(ModelError, match="MODEL_HOSTED_PROVIDER_REFUSED"):
        load_chat_model_config({"COUNTERSIGN_LLM_PROVIDER": "codex_cli", **hosted})


def test_explicit_hosted_loader_and_hosted_settings_refuse(monkeypatch):
    with pytest.raises(ModelError, match="MODEL_HOSTED_PROVIDER_REFUSED"):
        load_chat_model_config({"COUNTERSIGN_LLM_PROVIDER": "codex_cli"}, hosted=True)
    monkeypatch.setenv("COUNTERSIGN_LLM_PROVIDER", "codex_cli")
    with pytest.raises(SettingsError, match="refuses the local CLI provider"):
        load_hosted_settings()


def test_child_environment_contains_only_os_and_login_location():
    result = child_environment({"SYSTEMROOT": "C:/Windows", "USERPROFILE": "C:/fake",
        "CODEX_HOME": "C:/fake/login", "PAYPAL_CLIENT_SECRET": "must-not-copy",
        "GOOGLE_API_KEY": "must-not-copy", "OPENAI_API_KEY": "must-not-copy",
        "OPEN_ROUTER_API_KEY": "must-not-copy", "PATH": "untrusted-tools"})
    assert result == {"SYSTEMROOT": "C:/Windows", "USERPROFILE": "C:/fake", "CODEX_HOME": "C:/fake/login"}


def test_required_flags_and_capabilities_are_explicit():
    args = command(Path("test.exe"), "local-test-model", Path("empty"), Path("schema"), Path("catalog"))
    for flag in ("--ephemeral", "--ignore-user-config", "--ignore-rules", "--skip-git-repo-check",
                 "--sandbox", "read-only", "--output-schema", "--json", "--strict-config"):
        assert flag in args
    assert "-C" in args and args[-1] == "-"
    assert 'web_search="disabled"' in args and 'mcp_servers={}' in args
    assert all(f"features.{name}=false" in args for name in DISABLED)
    assert not {"--worktree", "--approve-for-me", "--dangerously-bypass-approvals-and-sandbox"} & set(args)
    assert "exec_permission_approvals" in DISABLED
    assert not {"codex_hooks", "collab", "connectors", "web_search_cached", "web_search_request",
                "imagegenext", "memory_tool", "request_permissions", "web_search"} & set(DISABLED)


def test_startup_catalog_retains_instructions_and_disables_file_edit_tools():
    original = {"models": [{"slug": "local-test-model", "base_instructions": "original CLI instructions",
        "apply_patch_tool_type": "freeform", "shell_type": "unified_exec",
        "experimental_supported_tools": ["clock"]}]}
    selected = isolated_catalog(original, "local-test-model")["models"][0]
    assert selected["base_instructions"] == original["models"][0]["base_instructions"]
    assert selected["shell_type"] == "disabled" and selected["apply_patch_tool_type"] is None
    assert selected["experimental_supported_tools"] == []
    assert original["models"][0]["apply_patch_tool_type"] == "freeform"


@pytest.mark.parametrize("kind", ["command_execution", "file_change", "web_search", "mcp_tool_call",
    "dynamic_tool_call", "collab_tool_call", "image_generation", "unknown_future_tool"])
@pytest.mark.parametrize("event", ["item.started", "item.completed"])
def test_any_native_tool_event_is_a_fixed_fatal_error(kind, event):
    with pytest.raises(ModelError, match="^MODEL_NATIVE_TOOL_FORBIDDEN$"):
        Events().accept({"type": event, "item": {"type": kind}})


def test_text_and_usage_events_are_accepted_without_exporting_reasoning():
    events = Events()
    for event in [{"type": "thread.started"}, {"type": "turn.started"},
        {"type": "item.completed", "item": {"type": "reasoning", "text": "private"}},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "{}"}},
        {"type": "turn.completed", "usage": {"input_tokens": 5, "output_tokens": 2}}]:
        events.accept(event)
    assert events.message == "{}" and events.usage["input_tokens"] == 5
    assert "private" not in json.dumps(events.__dict__)
    with pytest.raises(ModelError, match="MODEL_EVENT_FORBIDDEN"):
        events.accept({"type": "new_capability"})


def test_usage_stop_is_not_retried_or_silently_converted():
    with pytest.raises(ModelError, match="MODEL_QUOTA_EXHAUSTED"):
        Events().accept({"type": "turn.failed", "error": {"code": "usage_limit_reached"}})


def test_startup_error_items_are_counted_without_retaining_private_text():
    events = Events()
    events.accept({"type": "thread.started"})
    for _ in range(3):
        events.accept({"type": "item.completed", "item": {"type": "error",
            "message": "invented-warning-private-text"}})
    assert events.startup_warning_count == 3 and events.rejected_item_type is None
    assert "invented-warning-private-text" not in json.dumps(events.__dict__)
    events.accept({"type": "turn.started"})
    events.accept({"type": "item.completed", "item": {"type": "agent_message", "text": "{}"}})
    assert events.message == "{}"


@pytest.mark.parametrize("kind", ["item.started", "item.updated", "item.completed"])
def test_error_item_after_turn_started_is_fatal(kind):
    events = Events()
    events.accept({"type": "turn.started"})
    with pytest.raises(ModelError, match="^MODEL_CLI_ERROR_ITEM$"):
        events.accept({"type": kind, "item": {"type": "error", "message": "invented-private-text"}})
    assert events.rejected_item_type == "error" and events.startup_warning_count == 0
    assert "invented-private-text" not in json.dumps(events.__dict__)


def test_command_before_turn_started_still_aborts_after_startup_warning():
    events = Events()
    events.accept({"type": "thread.started"})
    events.accept({"type": "item.completed", "item": {"type": "error", "message": "ignored"}})
    with pytest.raises(ModelError, match="^MODEL_NATIVE_TOOL_FORBIDDEN$"):
        events.accept({"type": "item.started", "item": {"type": "command_execution", "command": "private"}})
    assert not events.turn_started and events.startup_warning_count == 1
    assert events.rejected_item_type == "command_execution"
    assert "private" not in json.dumps(events.__dict__)


def test_rejected_item_type_is_fixed_name_or_unknown_only():
    events = Events()
    with pytest.raises(ModelError, match="MODEL_NATIVE_TOOL_FORBIDDEN"):
        events.accept({"type": "item.completed", "item": {"type": "invented-private-type"}})
    assert events.rejected_item_type == "unknown"
    assert "invented-private-type" not in json.dumps(events.__dict__)


def test_write_tool_schema_cannot_enter_cli_request():
    with pytest.raises(ModelError, match="MODEL_TOOL_NOT_ALLOWED"):
        schema_for([{"function": {"name": "accept_dispute_claim", "parameters": {}}}])


@pytest.mark.asyncio
async def test_client_without_zero_tool_proof_cannot_start_process():
    client = CodexCLIClient(binary="missing", model="test", catalog={})
    with pytest.raises(ModelError, match="MODEL_TOOL_DISABLE_PROOF_REQUIRED"):
        await client.ainvoke([])


@pytest.mark.parametrize("item,forbidden,expected", [
    ({"type": "command_execution", "command": "read outside"}, None, "MODEL_NATIVE_TOOL_FORBIDDEN"),
    ({"type": "agent_message", "text": "invented-unit-canary-value"},
     "invented-unit-canary-value", "MODEL_CANARY_DISCLOSED"),
])
def test_forbidden_events_kill_child_without_retry_or_private_diagnostic(monkeypatch, item, forbidden, expected):
    from countersign.agent import codex_cli

    processes = []
    class Process:
        def __init__(self, args, **options):
            assert Path(options["cwd"]).is_dir() and not list(Path(options["cwd"]).iterdir())
            assert not any("KEY" in name or "PAYPAL" in name for name in options["env"])
            self.stdout = io.BytesIO((json.dumps({"type": "item.started", "item": item}) + "\n").encode())
            self.stderr, self.stdin = io.BytesIO(b"private diagnostic"), io.BytesIO()
            self.returncode, self.killed = None, False
            processes.append(self)
        def kill(self):
            self.killed, self.returncode = True, -9
        def poll(self):
            return self.returncode
        def wait(self, **_kwargs):
            self.returncode = self.returncode if self.returncode is not None else 0
            return self.returncode
    monkeypatch.setattr(codex_cli.subprocess, "Popen", Process)
    client = CodexCLIClient(binary="invented.exe", model="local-test-model", proof=True,
                            catalog={"models": [{"slug": "local-test-model"}]})
    client.forbidden_output = forbidden
    with pytest.raises(ModelError, match="^" + expected + "$") as error:
        client.invoke("Unit test prompt")
    assert len(processes) == 1 and processes[0].killed
    assert "private diagnostic" not in str(error.value)
    assert client.last_event_metadata["startup_warning_count"] == 0
    if item["type"] == "command_execution":
        assert client.last_event_metadata["rejected_item_type"] == "command_execution"
