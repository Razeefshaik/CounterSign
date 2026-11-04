"""The stage-one runner records an abort, never retries or starts later stages."""

import json
from types import SimpleNamespace

from countersign.agent.models import ModelError
from scripts import c67_cli_stage1 as stage


def test_native_tool_abort_is_saved_as_failed_run(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(stage, "RECORD", tmp_path / "run.json")
    monkeypatch.setattr(stage, "find_binary", lambda: "invented-cli")
    monkeypatch.setattr("subprocess.run", lambda *_a, **_k: SimpleNamespace(returncode=0,
        stdout=json.dumps({"models": [{"slug": "test-model", "priority": 1, "visibility": "list"}]})))
    calls = []
    async def fail(record):
        calls.append(1)
        record["turns"].append({"number": 1, "status": "started"})
        raise ModelError("MODEL_NATIVE_TOOL_FORBIDDEN")
    monkeypatch.setattr(stage, "stage_one", fail)
    assert stage.main() == 1 and len(calls) == 1
    record = json.loads(stage.RECORD.read_text())
    assert record["status"] == "failed" and record["error"] == "MODEL_NATIVE_TOOL_FORBIDDEN"
    assert record["turns"][0]["status"] == "failed"
    assert not record["week_run"] and not record["pilot_run"] and not record["evaluation_run"]
    assert stage.main() == 1 and len(calls) == 1
    assert "private" not in capsys.readouterr().out
