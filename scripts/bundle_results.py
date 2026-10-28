"""Generate public story data from committed evaluation and sandbox findings.

No environment files, model providers, database or PayPal access. This module
reads local published evidence only; all output is explicitly labelled by ground.
"""

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = "eval/results/2026-10-27-model-none-c31"
# Targeted model-off reruns after RESULTS, applied in this order.
RERUNS = ("eval/results/2026-10-27-m21-three-gaps", "eval/results/2026-10-27-c33-failed-replacement")


def runs_of(folder):
    return [json.loads(line) for line in (folder / "runs.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()]


def per_scenario(rows):
    passing = defaultdict(int)
    for row in rows:
        if row["system"] == "countersign":
            passing[row["scenario_id"]] += row["passed"]
    return passing


def targeted(root):
    """Overlay each rerun's CounterSign scenarios on the full run; the total is implied, not measured."""
    full = runs_of(root / RESULTS)
    current = per_scenario(full)
    reruns = []
    for source in RERUNS:
        rows = runs_of(root / source)
        if any(r["ground"] != "mock" or r["model"] != "none" for r in rows):
            raise ValueError("RERUN_NOT_MODEL_OFF_MOCK")
        after = per_scenario(rows)
        changed = [{"scenario": s, "before": current[s], "after": n, "runs": 5}
                   for s, n in sorted(after.items()) if n != current[s]]
        current.update(after)
        reruns.append({"source": source + "/README.md", "scenarios_rerun": len(after),
                       "changed": changed})
    return {"system": "countersign", "ground": "mock", "model": "none", "reruns": reruns,
            "implied_passing": sum(current.values()),
            "runs": sum(r["system"] == "countersign" for r in full), "measured": False,
            "label": "Full run plus targeted reruns; the full evaluation was not rerun"}


def scoreboard(folder):
    summaries = json.loads((folder / "summary.json").read_text(encoding="utf-8"))
    runs = [json.loads(line) for line in (folder / "runs.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()]
    result = []
    for summary in summaries:
        rows = [r for r in runs if r["system"] == summary["system"] and r["ground"] == "mock"]
        if len(rows) != summary["runs"] or summary["ground"] != "mock" or summary["k"] != 5:
            raise ValueError("RESULTS_SOURCE_MISMATCH")
        scenarios = defaultdict(list)
        for row in rows:
            scenarios[row["scenario_id"]].append(row)
        if any(len(group) != 5 or {r["k"] for r in group} != set(range(5))
               for group in scenarios.values()):
            raise ValueError("RESULTS_REPETITIONS_INCOMPLETE")
        passed = sum(r["passed"] for r in rows)
        consistent = sum(all(r["passed"] for r in group) for group in scenarios.values())
        if (abs(passed / len(rows) - summary["pass_rate"]) > 1e-12
                or abs(consistent / len(scenarios) - summary["pass_hat_k"]) > 1e-12):
            raise ValueError("RESULTS_SUMMARY_MISMATCH")
        invariants = {key: sum(r["invariants"][key] for r in rows)
                      for key in summary["invariant_violations"]}
        if invariants != summary["invariant_violations"]:
            raise ValueError("RESULTS_INVARIANT_MISMATCH")
        result.append({"system": summary["system"], "ground": "mock", "model": "none",
            "passing": passed, "runs": len(rows), "scenarios": len(scenarios), "k": 5,
            "pass_5_scenarios": consistent, "error_rows": sum(r["error"] is not None for r in rows),
            "invariants": invariants, "source": RESULTS + "/summary.json"})
    return result


def history(readme):
    result = []
    for line in readme.splitlines():
        if not line.startswith(("| rules_only |", "| countersign (model disabled) |")):
            continue
        fields = [field.strip() for field in line.strip("|").split("|")]
        system = fields[0].split()[0]
        for label, value in zip(("original", "after_pr_07", "after_c31_and_reserves"), fields[1:4], strict=True):
            passing, runs = map(int, value.split("/"))
            result.append({"system": system, "experiment": label, "passing": passing,
                "runs": runs, "ground": "mock", "model": "none",
                "source": RESULTS + "/README.md"})
    if len(result) != 6:
        raise ValueError("RESULTS_HISTORY_MISSING")
    return result


def sandbox_findings(markdown):
    result = []
    for line in markdown.splitlines():
        fields = [part.strip() for part in line.strip("|").split("|")]
        if not line.startswith("| D") or len(fields) != 6:
            continue
        identifier, claim, observed, evidence, verdict, _ = fields
        if "DIFFERENT" not in verdict and "CONFIRMED" not in verdict:
            continue
        paths = re.findall(r"`([^`]+)`", evidence)
        paths = ["recordings/sandbox/" + path for path in paths
                 if not path.startswith("e_")]
        result.append({"id": identifier, "claim": claim, "observed": observed,
                       "evidence_path": paths})
    resolved = markdown.partition("## Resolved after the first write-up")[2]
    for number, match in enumerate(re.finditer(r"^- \*\*([^*]+)\*\* (.+)$", resolved, re.MULTILINE), 1):
        result.append({"id": "D8-D12-resolved-" + str(number), "claim": match[1],
            "observed": match[2], "evidence_path": ["recordings/sandbox/balances.jsonl"]})
    return result


def bundle(root=ROOT):
    folder = root / RESULTS
    # A configured folder is included only if its published rows actually name a
    # real model, never because a directory's name happens to say "configured".
    configured = []
    for candidate in sorted((root / "eval/results").glob("*/runs.jsonl")):
        rows = [json.loads(line) for line in candidate.read_text(encoding="utf-8").splitlines()
                if line.strip()]
        if rows and all(r.get("model") not in {None, "none", "scripted"} for r in rows):
            configured.append({"source": str(candidate.parent.relative_to(root)).replace("\\", "/"),
                               "runs": len(rows), "models": sorted({r["model"] for r in rows})})
    return {"model_off": {"ground": "mock", "model": "none", "staged": True,
                "label": "Model-disabled mock evaluation; not a real-model or sandbox run",
                "scoreboard": scoreboard(folder)},
            "history": history((folder / "README.md").read_text(encoding="utf-8")),
            "targeted": targeted(root),
            "real_model": configured or None,
            "sandbox_findings": sandbox_findings((root / "docs/SANDBOX_FINDINGS.md").read_text(encoding="utf-8")),
            "limitations": ["Mock and sandbox evidence are separate.",
                "Zero measured invariant counts do not establish universal safety.",
                "Disputes opened on captures already settled into available remain untested."]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "web/results/summary.json")
    args = parser.parse_args(argv)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(bundle(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
