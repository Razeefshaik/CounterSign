"""M18 orchestration tests use fake systems/models; never a provider or PayPal."""

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from eval.evaluate import evaluate, load_scenarios
from eval.metrics import INVARIANTS
from eval.runner import QUOTA_EXHAUSTED, ModelQuotaExhausted

ROOT = Path(__file__).resolve().parents[2]


def scenario():
    return json.loads((ROOT / "eval/scenarios/F01_timeout_after_payout_created.json").read_text())


def factories(configured=False):
    names = (
        ["rules_only", "baseline_llm", "countersign"]
        if configured
        else ["rules_only", "countersign"]
    )
    return {name: lambda name=name: SimpleNamespace(name=name) for name in names}


class FakeModel:
    model_id = "fake-gemini"

    async def complete(self, request):
        raise AssertionError("Orchestration tests must not call the model")


class FakeRunner:
    def __init__(self, quota_at=None):
        self.calls = []
        self.quota_at = quota_at

    async def __call__(self, doc, system, **kwargs):
        self.calls.append((system.name, doc["id"], kwargs))
        is_model = kwargs["llm"] is not None
        return {
            "run_id": kwargs["run_id"],
            "system": system.name,
            "scenario_id": doc["id"],
            "fault_class": doc["fault_class"],
            "provenance": doc["provenance"],
            "ground": "mock",
            "k": kwargs["k_index"],
            "seed": doc["seed"],
            "model": kwargs["model"],
            "invariants": dict.fromkeys(INVARIANTS, 0),
            "passed": True,
            "correct_final_action": True,
            "task_completed": True,
            "makers_paid_on_time": {"paid": 1, "due": 1},
            "owner_escalations": 1,
            "unnecessary_escalations": 0,
            "llm_calls": int(is_model),
            "tokens": {"in": 12 if is_model else 0, "out": 3 if is_model else 0},
            "duration_ms": 1,
            "error": QUOTA_EXHAUSTED if len(self.calls) == self.quota_at else None,
        }


@pytest.mark.asyncio
async def test_none_runs_exact_cartesian_product_without_a_model(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "secret-not-for-none-mode")
    fake = FakeRunner()
    rows = await evaluate([scenario()], output=tmp_path, k=3, factories=factories(), runner=fake)
    assert len(rows) == len(fake.calls) == 6
    assert all(call[2]["llm"] is None and call[2]["model"] == "none" for call in fake.calls)
    assert all(call[2]["mock_transport"] == "asgi" for call in fake.calls)
    assert {path.name for path in tmp_path.iterdir()} == {
        "checkpoint.json",
        "runs.jsonl",
        "summary.json",
        "report.md",
    }
    checkpoint = (tmp_path / "checkpoint.json").read_text()
    assert "secret-not-for-none-mode" not in checkpoint
    report = (tmp_path / "report.md").read_text()
    assert "model was disabled" in report and "Owner interruptions" in report
    assert all(invariant in report for invariant in INVARIANTS)
    resumed = FakeRunner()
    assert (
        await evaluate(
            [scenario()], output=tmp_path, k=3, resume=True, factories=factories(), runner=resumed
        )
        == rows
    )
    assert resumed.calls == []


@pytest.mark.asyncio
async def test_configured_shares_one_model_and_quota_retry_does_not_skip_cell(tmp_path):
    model = FakeModel()
    fake = FakeRunner(quota_at=3)
    kwargs = {
        "output": tmp_path,
        "k": 2,
        "model": "configured",
        "llm": model,
        "factories": factories(True),
    }
    with pytest.raises(ModelQuotaExhausted):
        await evaluate([scenario()], runner=fake, **kwargs)
    assert not (tmp_path / "summary.json").exists()
    assert not (tmp_path / "report.md").exists()
    checkpoint = json.loads((tmp_path / "checkpoint.json").read_text())
    assert len(checkpoint["rows"]) == 2 and len(checkpoint["quota_stops"]) == 1
    assert checkpoint["quota_stops"][0]["system"] == "baseline_llm"
    retry = FakeRunner()
    rows = await evaluate([scenario()], runner=retry, resume=True, **kwargs)
    assert len(rows) == 6 and len(retry.calls) == 4
    assert retry.calls[0][0] == "baseline_llm" and retry.calls[0][2]["k_index"] == 0
    assert all(call[2]["llm"] is model for call in retry.calls)
    assert all(call[2]["model"] == "fake-gemini" for call in retry.calls)
    assert all(call[2]["mock_transport"] == "tcp" for call in fake.calls + retry.calls)
    assert not (tmp_path / "runs.partial.jsonl").exists()
    report = (tmp_path / "report.md").read_text()
    assert "Quota-stopped attempts retried: 1" in report
    assert "not billing totals" in report


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["scenario", "k", "model", "mode"])
async def test_resume_refuses_different_experiment(tmp_path, changed):
    model = FakeModel()
    kwargs = {
        "output": tmp_path,
        "k": 1,
        "factories": factories(True),
        "model": "configured",
        "llm": model,
    }
    await evaluate([scenario()], runner=FakeRunner(), **kwargs)
    doc = scenario()
    if changed == "scenario":
        doc["seed"] += 1
    elif changed == "k":
        kwargs["k"] = 2
    elif changed == "model":
        kwargs["model_id"] = "different-model"
    else:
        kwargs.update(model="none", llm=None, factories=factories())
    with pytest.raises(ValueError, match="Checkpoint does not match"):
        await evaluate([doc], runner=FakeRunner(), resume=True, **kwargs)
    assert not (tmp_path / ".evaluation.lock").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("model", "changed"),
        ("ground", "sandbox"),
        ("seed", -1),
        ("run_id", "different"),
        ("scenario_id", "absent"),
    ],
)
async def test_resume_rejects_tampered_row_metadata(tmp_path, field, value):
    kwargs = {"output": tmp_path, "k": 1, "factories": factories(), "runner": FakeRunner()}
    await evaluate([scenario()], **kwargs)
    path = tmp_path / "checkpoint.json"
    checkpoint = json.loads(path.read_text())
    checkpoint["rows"][0][field] = value
    path.write_text(json.dumps(checkpoint))
    with pytest.raises(ValueError):
        await evaluate([scenario()], resume=True, **kwargs)


@pytest.mark.asyncio
async def test_resume_rejects_duplicate_cell_and_changed_code_identity(tmp_path):
    kwargs = {"output": tmp_path, "k": 1, "factories": factories(), "runner": FakeRunner()}
    await evaluate([scenario()], **kwargs)
    path = tmp_path / "checkpoint.json"
    checkpoint = json.loads(path.read_text())
    original = deepcopy(checkpoint)
    checkpoint["rows"].append(checkpoint["rows"][0])
    path.write_text(json.dumps(checkpoint))
    with pytest.raises(ValueError, match="Invalid checkpoint cell"):
        await evaluate([scenario()], resume=True, **kwargs)
    original["manifest"]["code"] = "not-this-source"
    path.write_text(json.dumps(original))
    with pytest.raises(ValueError, match="Checkpoint does not match"):
        await evaluate([scenario()], resume=True, **kwargs)


@pytest.mark.asyncio
async def test_hard_crash_lock_is_not_automatically_removed(tmp_path):
    lock = tmp_path / ".evaluation.lock"
    lock.touch()
    with pytest.raises(FileExistsError):
        await evaluate([scenario()], output=tmp_path, factories=factories(), runner=FakeRunner())
    assert lock.exists()


@pytest.mark.asyncio
async def test_changed_mock_source_refuses_resume(tmp_path, monkeypatch):
    import eval.evaluate as module

    source = tmp_path / "source"
    mock = source / "mock_paypal/resources.py"
    mock.parent.mkdir(parents=True)
    mock.write_text("SNAPSHOT_LIMIT = 256\n")
    monkeypatch.setattr(module, "ROOT", source)
    kwargs = {"output": tmp_path / "results", "factories": factories(), "runner": FakeRunner()}
    await evaluate([scenario()], **kwargs)
    mock.write_text("SNAPSHOT_LIMIT = 1\n")
    with pytest.raises(ValueError, match="Checkpoint does not match"):
        await evaluate([scenario()], resume=True, **kwargs)


def test_default_corpus_excludes_sandbox_and_explicit_selection_refuses_it(tmp_path, monkeypatch):
    import eval.evaluate as module

    folder = tmp_path / "eval/scenarios"
    folder.mkdir(parents=True)
    mock = scenario()
    sandbox = deepcopy(mock)
    sandbox.update(id="sandbox_case", ground="sandbox")
    sandbox["timeline"] = [
        step for step in sandbox["timeline"] if step["do"] not in {"fault", "webhook_fault", "mock"}
    ]
    (folder / "mock.json").write_text(json.dumps(mock))
    path = folder / "sandbox_case.json"
    path.write_text(json.dumps(sandbox))
    monkeypatch.setattr(module, "ROOT", tmp_path)
    assert load_scenarios() == [mock]
    with pytest.raises(ValueError, match="Explicit sandbox"):
        load_scenarios([path])


@pytest.mark.asyncio
async def test_quota_is_preserved_in_counter_sign_meter():
    from countersign.agent.models import ModelError
    from eval.systems.base import SystemTrace
    from eval.systems.countersign import _MeasuredModel

    class Quota(Exception):
        status_code = 429

    for error in [Quota(), ModelError("MODEL_QUOTA_EXHAUSTED")]:

        class Exhausted:
            async def complete(self, request, failure=error):
                raise RuntimeError("private provider text") from failure

        trace = SystemTrace([], 0, [], 0, 0, 0)
        meter = _MeasuredModel(Exhausted(), trace)
        with pytest.raises(RuntimeError):
            await meter.complete({})
        assert trace.errors == [QUOTA_EXHAUSTED]
        assert trace.llm_calls == 1


@pytest.mark.asyncio
async def test_counter_sign_tick_stops_immediately_after_caught_provider_quota():
    from eval.systems.countersign import CountersignSystem, _MeasuredModel

    system = CountersignSystem(database_url="unused")

    class Quota(Exception):
        status_code = 429

    class Exhausted:
        async def complete(self, request):
            raise Quota()

    meter = _MeasuredModel(Exhausted(), system.trace)

    class Engine:
        async def tick(self):
            try:
                await meter.complete({})
            except Quota:
                pass  # The real commander records a generic failure and returns.
            return {"errors": 1}

    system._engine = Engine()
    with pytest.raises(RuntimeError, match="MODEL_QUOTA_EXHAUSTED"):
        await system._tick()
    assert system.trace.errors == [QUOTA_EXHAUSTED]
