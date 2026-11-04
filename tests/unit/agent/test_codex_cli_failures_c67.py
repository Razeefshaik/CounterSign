"""Distinct failure codes and bounded private diagnostics; no provider calls."""

import io
import json

import pytest

from countersign.agent import codex_cli
from countersign.agent import codex_cli_diagnostics as diagnostics
from countersign.agent.models import ModelError


@pytest.mark.parametrize("case,expected", [
    ("turn", "MODEL_CLI_TURN_FAILED"), ("exit", "MODEL_CLI_EXIT_NONZERO"),
    ("empty", "MODEL_CLI_NO_MESSAGE"), ("json", "MODEL_OUTPUT_NOT_JSON"),
    ("schema", "MODEL_OUTPUT_SCHEMA_INVALID"), ("spawn", "MODEL_CLI_SPAWN_FAILED"),
])
def test_fixed_failure_codes_are_distinct_and_detail_is_local_only(monkeypatch, tmp_path, case, expected):
    monkeypatch.setattr(diagnostics, "DIRECTORY", tmp_path)
    planted_key = "sk-proj-" + "x" * 40
    private = planted_key + " someone@private-company.invalid " + "detail " * 150
    events = [{"type": "turn.started"}]
    if case == "turn":
        events += [{"type": "turn.failed", "error": {"code": "invalid_json_schema", "message": private}}]
    elif case not in {"empty", "exit", "spawn"}:
        text = "not JSON" if case == "json" else '{}'
        events += [{"type": "item.completed", "item": {"type": "agent_message", "text": text}}]
    events += [{"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 1}}]
    processes = []
    class Process:
        def __init__(self, *_args, **_kwargs):
            if case == "spawn":
                raise OSError(private)
            self.stdout = io.BytesIO("".join(json.dumps(e) + "\n" for e in events).encode())
            self.stderr = io.BytesIO(("Error: " + private).encode()) if case == "exit" else io.BytesIO()
            self.stdin = io.BytesIO()
            self.returncode = 1 if case == "exit" else 0
            processes.append(self)
        def kill(self):
            self.returncode = -9
        def poll(self):
            return self.returncode
        def wait(self, **_kwargs):
            return self.returncode
    monkeypatch.setattr(codex_cli.subprocess, "Popen", Process)
    client = codex_cli.CodexCLIClient(binary="unused", model="test", proof=True,
                                    catalog={"models": [{"slug": "test"}]})
    with pytest.raises(ModelError, match="^" + expected + "$") as error:
        client.invoke("harmless offline test")
    assert len(processes) <= 1
    assert str(error.value) == expected
    text = client.last_local_error_path.read_text()
    assert len(text) <= 500 and planted_key not in text and "private-company.invalid" not in text
    assert len(list(tmp_path.iterdir())) == 1
    if case == "turn":
        assert client.last_event_metadata["provider_error_code"] == "invalid_json_schema"
        assert "detail" not in json.dumps(client.last_event_metadata)


@pytest.mark.parametrize("value", ["has whitespace", "x" * 65, "sk-proj-" + "q" * 40, "line\nbreak", None])
def test_provider_error_code_is_only_a_short_non_private_token(value):
    assert diagnostics.provider_code(value) is None
    assert diagnostics.provider_code("invalid_json_schema") == "invalid_json_schema"


def test_embedded_provider_error_code_is_kept_without_exporting_message(monkeypatch, tmp_path):
    monkeypatch.setattr(diagnostics, "DIRECTORY", tmp_path)
    message = 'HTTP 400: ' + json.dumps({"error": {"code": "invalid_json_schema",
                                                 "message": "Unsupported schema keyword"}})
    events = codex_cli.Events()
    with pytest.raises(ModelError, match="^MODEL_CLI_TURN_FAILED$") as error:
        events.accept({"type": "turn.failed", "error": {"message": message}})
    assert events.provider_error_code == "invalid_json_schema"
    assert "Unsupported" not in json.dumps(events.__dict__)
    path = diagnostics.save_failure(error.value)
    assert path.read_text() == "Unsupported schema keyword"
