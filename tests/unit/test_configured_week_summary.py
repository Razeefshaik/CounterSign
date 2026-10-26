from types import SimpleNamespace

from scripts.demo_week import configured_safety


def test_stopped_or_legacy_checkpoint_explicitly_reports_unshown_safety_moments():
    summary = configured_safety(SimpleNamespace(data={}))
    assert summary == {name: {"shown": False}
                       for name in ("lost_response", "injection", "settled_retry")}


def test_resume_preserves_recorded_proof_without_claiming_unrecorded_moments():
    proof = {"shown": True, "source": "executor_identical_replay", "model_proposed_write": False}
    summary = configured_safety(SimpleNamespace(data={"configured_safety": {"lost_response": proof}}))
    assert summary["lost_response"] == proof
    assert summary["injection"] == summary["settled_retry"] == {"shown": False}
