"""Opt-in mock-week evidence from actual persisted agent proposals and verdicts."""

from countersign.agent.safety import sanitize
from scripts.bundle_eval_runs import private_guard


def project_decision(doc, trace):
    """Allowlist recorded facts; no reasoning signatures or raw model traffic."""
    value = {key: doc.get(key) for key in (
        "id", "incident_id", "created_at", "action", "params", "evidence_refs",
        "rationale", "flags", "model", "verdict", "rule_results", "state")}
    value["reads"] = trace.get("calls")
    value["rounds"] = trace.get("rounds")
    value = sanitize(value)
    private_guard(value)
    return value


async def capture_decisions(engine, store):
    if not getattr(store, "c67_record_decisions", False):
        return
    await store.c67_recorder.capture(engine)
