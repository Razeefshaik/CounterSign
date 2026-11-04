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
    seen = getattr(store, "c67_seen", set())
    async with engine.database.tx() as conn:
        cursor = await conn.execute(
            "SELECT v.doc,p.raw_output->'agent_trace' AS trace "
            "FROM proposals p JOIN v_dash_proposals v ON v.id=p.id "
            "WHERE p.raw_output ? 'agent_trace' ORDER BY p.created_at,p.id")
        for row in await cursor.fetchall():
            doc = row["doc"]
            identity = (doc["id"], doc["state"], doc.get("verdict"))
            if identity not in seen:
                store.record("RECORDED_MODEL_DECISION", decision=project_decision(doc, row["trace"]))
                seen.add(identity)
    store.c67_seen = seen
