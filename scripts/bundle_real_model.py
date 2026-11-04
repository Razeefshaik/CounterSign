"""C67 allowlisted model evidence; separate datasets, recomputed result totals."""

import json
from pathlib import Path

from eval.metrics import INVARIANTS
from eval.report import summarize
from scripts.bundle_eval_runs import private_guard

ROOT = Path(__file__).resolve().parents[1]
EARLIER = "recordings/real_model/2026-10-27"
CURRENT = "recordings/real_model/2026-11-04-c67"
RESULTS = "eval/results/2026-11-04-c67-gemma"
EVIDENCE = "design-demos/reference/c67"
STAGE_ONE = "recordings/real_model/2026-11-04-c67-cli-stage1/run.json"
FIELDS = ("system", "scenario_id", "k", "fault_class", "provenance", "passed",
          "correct_final_action", "task_completed", "invariants", "makers_paid_on_time",
          "owner_escalations", "duration_ms", "model", "ground", "llm_calls", "tokens", "error")


def validated_evaluation(rows, *, k, scenario_ids, model):
    """Refuse partial comparisons, wrong identities or invalid summary counters."""
    private_guard(rows)
    if type(k) is not int or not 1 <= k <= 5 or not scenario_ids:
        raise ValueError("C67_RESULTS_INVALID")
    systems = {"countersign", "baseline_llm", "rules_only"}
    expected = {(system, scenario, index) for system in systems
                for scenario in scenario_ids for index in range(k)}
    seen, clean = set(), []
    for row in rows:
        cell = (row["system"], row["scenario_id"], row["k"])
        if (cell not in expected or cell in seen or row["ground"] != "mock"
                or row["model"] != ("none" if row["system"] == "rules_only" else model)
                or set(row["invariants"]) != set(INVARIANTS)
                or any(type(v) is not int or v < 0 for v in row["invariants"].values())
                or any(type(row[key]) is not bool for key in
                       ("passed", "correct_final_action", "task_completed"))
                or (row["passed"] and (row["error"] is not None
                    or not row["task_completed"] or not row["correct_final_action"]
                    or any(row["invariants"].values())))):
            raise ValueError("C67_RESULTS_INVALID")
        seen.add(cell)
        clean.append({key: row[key] for key in FIELDS})
    if seen != expected:
        raise ValueError("C67_RESULTS_INCOMPLETE")
    return {"runs": clean, "summary": summarize(rows, k)}


def earlier_week(root):
    rows = [json.loads(line) for line in (root / EARLIER / "run.jsonl").read_text().splitlines()]
    identity = next(row for row in rows if row["event"] == "DEMO_MODEL")
    readme_rows = [line.strip("|").split("|") for line in
                  (root / EARLIER / "README.md").read_text().splitlines()
                  if line.startswith("| ")]
    decisions = []
    for number, row in enumerate(rows, 1):
        if row["event"] not in {"AGENT_DECISION", "CONFIGURED_MODEL_ACTED_EARLIER",
                                "CONFIGURED_MODEL_DECISION_DIFFERS"}:
            continue
        if not row.get("action"):
            continue
        decisions.append({"id": row.get("proposal_id"), "created_at": row["at"],
            "action": row["action"], "step": row.get("step"), "params": None,
            "reads": None, "rationale": None, "flags": None,
            "verdict": row.get("verdict"), "rule_results": row.get("rules"),
            "recorded_outcome": next((parts[1].strip() for parts in readme_rows
                if parts[0].strip().startswith({"injection": "Injection bait",
                    "settled_retry": "Settled-retry bait"}.get(row.get("step"), row["action"]))), None),
            "source": EARLIER + "/run.jsonl", "line": number})
    return {"id": "gemini-2026-10-27", "date": "2026-10-27",
        "label": "Earlier real AI agent week",
        "provider_label": "Gemini via Google", "model": identity["model"],
        "ground": "mock", "status": "COMPLETE_WITH_GAPS", "decisions": decisions,
        "note": "Reads, proposal parameters, rationale and flags were not recorded. "
                "The AI agent chose WAIT for both baits; no shield BLOCK is recorded for them.",
        "source": EARLIER + "/README.md"}


def bundle(root=ROOT):
    # Nov 5 owner scope: stage 1 only; no older provider attempt in the panel.
    stage = {"status": "not_run", "round_trip": "not_run", "canary": {"status": "not_run"},
             "turns": [], "source": STAGE_ONE}
    if (root / STAGE_ONE).exists():
        saved = json.loads((root / STAGE_ONE).read_text())
        stage.update({key: saved[key] for key in ("status", "round_trip", "canary", "turns")})
        stage["run_details"] = f"{saved['model']} · {saved['cli_version']} · {saved['date']}"
    dataset = {"id": "c67-real-model", "label": "Real AI agent, mock PayPal",
        "ground": "mock", "status": "not_run", "runs": [], "summary": [], "k": 1,
        "reason": "Stage 1 covers access and canary checks only. No new week, pilot or evaluation has run."}
    value = {"schema_version": 1, "weeks": [earlier_week(root)],
             "smokes": [], "stage_one": stage, "datasets": [dataset]}
    private_guard(value)
    return value


def main():
    value = bundle()
    path = ROOT / "web/results/real-model-evidence.json"
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8", newline="\n")
    print("C67 public evidence bundled; model-disabled history unchanged")


if __name__ == "__main__":
    main()
