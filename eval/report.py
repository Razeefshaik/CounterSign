"""Write CONTRACTS 6.3 results. No traces, credentials or raw errors are published."""

import json
from collections import defaultdict
from pathlib import Path

from eval.metrics import INVARIANTS


def summarize(rows: list[dict], k: int) -> list[dict]:
    groups = defaultdict(list)
    for row in rows:
        groups[row["system"], row["ground"]].append(row)
    result = []
    for (system, ground), records in sorted(groups.items()):
        scenarios, provenance = defaultdict(list), defaultdict(list)
        for row in records:
            scenarios[row["scenario_id"]].append(row)
            provenance[row["provenance"]].append(row)
        if any(sorted(r["k"] for r in runs) != list(range(k)) for runs in scenarios.values()):
            raise ValueError("Incomplete or duplicate repetitions")
        rate = lambda field, records=records: sum(bool(r[field]) for r in records) / len(records)
        due = sum(r["makers_paid_on_time"]["due"] for r in records)
        result.append(
            {
                "system": system,
                "ground": ground,
                "scenarios": len(scenarios),
                "runs": len(records),
                "k": k,
                "invariant_violations": {
                    key: sum(r["invariants"][key] for r in records) for key in INVARIANTS
                },
                "pass_rate": rate("passed"),
                "pass_hat_k": sum(all(r["passed"] for r in runs) for runs in scenarios.values())
                / len(scenarios),
                "correct_final_action_rate": rate("correct_final_action"),
                "task_completion_rate": rate("task_completed"),
                "on_time_rate": sum(r["makers_paid_on_time"]["paid"] for r in records) / due
                if due
                else 0,
                "unnecessary_escalations_per_run": sum(
                    r["unnecessary_escalations"] for r in records
                )
                / len(records),
                "by_provenance": {
                    name: {
                        "runs": len(runs),
                        "pass_rate": sum(r["passed"] for r in runs) / len(runs),
                    }
                    for name, runs in sorted(provenance.items())
                },
            }
        )
    return result


def markdown(summary: list[dict], rows: list[dict]) -> str:
    safe = lambda text: (
        str(text).replace("|", "\\|").replace("\n", " ").replace("\r", " ").replace("<", "&lt;")
    )
    lines = [
        "# CounterSign evaluation",
        "",
        "Models: " + ", ".join(sorted({safe(r["model"]) for r in rows})),
        "",
    ]
    for ground in sorted({s["ground"] for s in summary}):
        lines += [
            f"## {ground}",
            "",
            "| System | Runs | Pass rate | pass^k | Task completion | Invariant violations |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
        for group in summary:
            if group["ground"] != ground:
                continue
            lines.append(
                f"| {safe(group['system'])} | {group['runs']} | {group['pass_rate']:.3f} | {group['pass_hat_k']:.3f} | {group['task_completion_rate']:.3f} | {sum(group['invariant_violations'].values())} |"
            )
            lines += [
                "",
                f"Provenance ({safe(group['system'])}): "
                + "; ".join(
                    f"{name}: {v['runs']} runs, pass rate {v['pass_rate']:.3f}"
                    for name, v in group["by_provenance"].items()
                ),
                "",
            ]
    if any(r["system"] == "baseline_llm" for r in rows):
        lines += [
            "The baseline ran only against the local mock with documented idempotency handling; it received no sandbox credentials."
        ]
    else:
        lines += [
            "The LLM baseline was not run. Its required scope is local mock only, with documented idempotency handling and no sandbox credentials."
        ]
    lines += [
        "",
        f"Runs with errors: {sum(bool(r['error']) for r in rows)}. These runs fail; zero invariant counters on an ungraded/error row do not certify safety.",
        "",
        "Disputed-share metric limitation: mock ground truth lacks order allocations; detection uses scenario-authorised maker totals as a bound. A partial payout within that bound cannot be attributed to an order.",
        "",
        "These are mock results, not sandbox verification. The dummy system is only a harness smoke check.",
        "",
    ]
    return "\n".join(lines)


def write_results(rows: list[dict], folder: str | Path, k: int) -> list[dict]:
    summary = summarize(rows, k)
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "runs.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8"
    )
    (folder / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    (folder / "report.md").write_text(markdown(summary, rows), encoding="utf-8")
    return summary
