"""Budget, missing-field and failure-latch checks; never contacts a provider."""

import json

import pytest

from countersign.agent.models import ModelError
from scripts.c67_week_recording import RecordedModel, WeekRecorder, public, result_summary
from scripts.sandbox_checks.redact import Redactor
from scripts.slice_e2e import Checkpoint


def store_at(tmp_path):
    return Checkpoint(tmp_path, target="mock", identity={"test": "C67"}, redactor=Redactor(), output=lambda _: None)


def request():
    return {"data": {"incident": {"id": "offline-case"}}, "observations": [], "tools": [], "system": "unchanged"}


@pytest.mark.asyncio
async def test_missing_model_fields_stay_absent_and_no_event_log_is_written(tmp_path):
    store = store_at(tmp_path)
    class Inner:
        model_id = "scripted"
        async def complete(self, req):
            assert req == request()
            return {"action": "WAIT", "params": {}}
    recorder = WeekRecorder(store)
    try:
        await RecordedModel(Inner(), recorder).complete(request())
        [row] = [json.loads(line) for line in (tmp_path / "decisions.jsonl").read_text().splitlines()]
        assert row["model"] == {"action": "WAIT", "params": {}}
        assert {"rationale", "flags", "injection_flags", "payee_change_flags"} <= set(row["absent_model_fields"])
        assert row["shield"]["verdict"] is None and row["usage"] is None
        assert not (tmp_path / "run.jsonl").exists()
    finally:
        store.close()


@pytest.mark.asyncio
async def test_hard_cap_is_sixty_and_persists_before_a_call(tmp_path):
    store = store_at(tmp_path)
    calls = []
    class Inner:
        model_id = "scripted"
        async def complete(self, req):
            calls.append(1)
            assert json.loads(store.path.read_text())["data"]["c67_model_turns"] == 60
            return {"action": "WAIT", "params": {}}
    try:
        store.checkpoint(c67_model_turns=59)
        recorder = WeekRecorder(store)
        model = RecordedModel(Inner(), recorder)
        await model.complete(request())
        with pytest.raises(ModelError, match="^MODEL_WEEK_TURN_CAP_EXHAUSTED$"):
            await model.complete(request())
        assert len(calls) == 1 and store.data["c67_model_turns"] == 60
    finally:
        store.close()


@pytest.mark.parametrize("code", ["MODEL_QUOTA_EXHAUSTED", "MODEL_NATIVE_TOOL_FORBIDDEN", "MODEL_CANARY_DISCLOSED"])
@pytest.mark.asyncio
async def test_failure_latches_and_never_retries(tmp_path, code):
    store = store_at(tmp_path)
    calls = []
    class Inner:
        model_id = "scripted"
        async def complete(self, req):
            calls.append(1)
            raise ModelError(code)
    try:
        recorder = WeekRecorder(store)
        model = RecordedModel(Inner(), recorder)
        for _ in range(2):
            with pytest.raises(ModelError, match="^" + code + "$"):
                await model.complete(request())
        assert len(calls) == 1 and recorder.turns[0]["status"] == "failed"
        assert recorder.turns[0]["error"] == code
    finally:
        store.close()


def test_read_summary_omits_buyer_text_and_public_output_redacts_planted_values():
    result = result_summary({"status": "OPEN", "dispute_id": "MOCK-ID", "messages": ["buyer free text"]})
    assert result == {"status": "OPEN", "dispute_id": "MOCK-ID"}
    planted = "sk-or-v1-" + "x" * 40
    output = public({"rationale": planted + " person@private-company.invalid"}, Redactor())
    assert planted not in json.dumps(output) and "private-company.invalid" not in json.dumps(output)


def test_week_launcher_does_not_inherit_provider_or_sandbox_credentials():
    from scripts.c67_stage2 import environment

    env = environment({"SYSTEMROOT": "C:/Windows", "CODEX_HOME": "C:/offline/login-location",
        "PAYPAL_CLIENT_ID": "never-inherit", "PAYPAL_CLIENT_SECRET": "never-inherit",
        "OPENAI_API_KEY": "never-inherit", "GOOGLE_API_KEY": "never-inherit",
        "OPEN_ROUTER_API_KEY": "never-inherit", "DATABASE_URL": "never-inherit",
        "COUNTERSIGN_HOSTED_START": "never-inherit"})
    assert env == {"SYSTEMROOT": "C:/Windows", "CODEX_HOME": "C:/offline/login-location"}
