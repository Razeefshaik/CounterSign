"""C58: publish an allowlisted, deterministic copy of the historical 480 mock runs.

Run from the repository: python scripts/bundle_eval_runs.py
No environment files, database, model, PayPal or network access.
"""

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path

if __package__:
    from .build_sandbox_proof import public_guard
else:
    from build_sandbox_proof import public_guard

ROOT = Path(__file__).resolve().parents[1]
SOURCE = "eval/results/2026-10-27-model-none-c31/runs.jsonl"
LABELS = ["mock", "model disabled", "not a real-model or sandbox run"]
FIELDS = (
    "system", "scenario_id", "k", "fault_class", "provenance", "passed",
    "correct_final_action", "task_completed", "invariants", "makers_paid_on_time",
    "owner_escalations", "duration_ms", "model", "ground",
)
INVARIANTS = (
    "duplicate_settlement", "payment_on_disputed_order_without_approval",
    "refund_without_owner_approval", "payment_to_unverified_payee",
)
TOKEN = re.compile(
    r"\b(?:AIza[A-Za-z0-9_-]{30,}|AQ\.[A-Za-z0-9_-]{30,}|"
    r"sk-(?:proj-)?[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[A-Z0-9]{16})\b"
)


def private_guard(value):
    public_guard(value)
    if isinstance(value, str) and TOKEN.search(value):
        raise ValueError("EVAL_PRIVATE_VALUE")
    if isinstance(value, dict):
        for key, item in value.items():
            private_guard(key)
            private_guard(item)
    elif isinstance(value, list):
        for item in value:
            private_guard(item)


def nonnegative(value):
    return type(value) is int and value >= 0


def project(row):
    private_guard(row)
    if not isinstance(row, dict):
        raise TypeError("EVAL_RUN_INVALID")
    if (row.get("system") not in {"countersign", "rules_only"}
            or row.get("ground") != "mock" or row.get("model") != "none"
            or not nonnegative(row.get("k")) or row["k"] > 4
            or any(type(row.get(key)) is not bool
                   for key in ("passed", "correct_final_action", "task_completed"))
            or any(not isinstance(row.get(key), str) or not row[key]
                   for key in ("scenario_id", "fault_class", "provenance"))
            or any(not nonnegative(row.get(key)) for key in ("owner_escalations", "duration_ms"))
            or not isinstance(row.get("invariants"), dict)
            or set(row["invariants"]) != set(INVARIANTS)
            or any(not nonnegative(row["invariants"][key]) for key in INVARIANTS)
            or not isinstance(row.get("makers_paid_on_time"), dict)
            or any(not nonnegative(row["makers_paid_on_time"].get(key)) for key in ("paid", "due"))
            or row["makers_paid_on_time"]["paid"] > row["makers_paid_on_time"]["due"]):
        raise ValueError("EVAL_RUN_INVALID")
    result = {key: row[key] for key in FIELDS}
    result["invariants"] = {key: row["invariants"][key] for key in INVARIANTS}
    result["makers_paid_on_time"] = {key: row["makers_paid_on_time"][key] for key in ("due", "paid")}
    return result


def bundle(root=ROOT):
    rows = [project(json.loads(line))
            for line in (root / SOURCE).read_text(encoding="utf-8").splitlines() if line.strip()]
    groups = defaultdict(list)
    for row in rows:
        groups[row["system"], row["scenario_id"]].append(row)
    if (len(rows) != 480 or len(groups) != 96
            or any(len(group) != 5 or {r["k"] for r in group} != set(range(5))
                   for group in groups.values())
            or {s for system, s in groups if system == "countersign"}
            != {s for system, s in groups if system == "rules_only"}):
        raise ValueError("EVAL_RUNS_INCOMPLETE")
    result = {"source": SOURCE, "labels": LABELS,
              "runs": sorted(rows, key=lambda row: (row["system"], row["scenario_id"], row["k"]))}
    private_guard(result)
    return result


def write_bundle(output, root=ROOT):
    # Fully validate before creating/replacing the output; never publish a partial artifact.
    content = json.dumps(bundle(root), sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content, encoding="utf-8", newline="\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "web/results/eval-runs.json")
    args = parser.parse_args(argv)
    try:
        write_bundle(args.output)
    except (OSError, ValueError, TypeError, KeyError):
        print("EVAL_BUNDLE_REFUSED: invalid or private-looking source; output unchanged")
        return 1
    print("Bundled 480 historical model-disabled mock runs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
