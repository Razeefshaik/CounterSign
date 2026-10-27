"""Build an honest model-disabled replay with the existing M11 runner.

Run from the repository root with an explicitly disposable _eval database:
python web/tests/build_replay.py --database-url postgresql://.../web_replay_eval
No runtime configuration file, model or PayPal sandbox is used.
"""

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from pathlib import Path

from eval.runner import run_one
from eval.systems.countersign import CountersignSystem
from eval.systems.rules_only import RulesOnlySystem

ROOT = Path(__file__).resolve().parents[2]


async def build(database_url):
    rows, evidence = [], []
    for scenario_id in ["F01_timeout_after_payout_created", "F_retry_settled_obligation"]:
        scenario = json.loads((ROOT / "eval/scenarios" / (scenario_id + ".json")).read_text())
        for system in [RulesOnlySystem(), CountersignSystem(database_url=database_url)]:
            original = system.teardown
            saved = {}

            async def capture(original=original, system=system, saved=saved):
                # Save public runner evidence before Engine teardown. Never grade from this trace.
                env = system.env if hasattr(system, "env") else system._env
                truth = await env.mock.ground_truth()
                saved["payout_items"] = [
                    {
                        "id": item["payout_item_id"],
                        "amount": item["amount"],
                        "status": item["status"],
                        "money_left_account": item["money_left_account"],
                        "created_at": item["created_at"],
                    }
                    for item in truth["payout_items"]
                ]
                trace = await original()
                # Only fixed action/outcome fields: params may contain addresses or private text.
                saved["actions"] = [
                    {key: asdict(action)[key] for key in ["at", "action", "outcome", "approved_by_owner"]}
                    for action in trace.actions
                ]
                return trace

            system.teardown = capture
            row = await run_one(scenario, system, run_id="m19-model-none", k_index=0, model="none")
            rows.append(row)
            evidence.append({
                "run_id": row["run_id"], "system": row["system"],
                "scenario_id": row["scenario_id"], "k": row["k"], "seed": row["seed"],
                "ground": "mock", **saved,
            })
    destination = ROOT / "web/replays"
    destination.mkdir(exist_ok=True)
    (destination / "runs.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8",
    )
    (destination / "evidence.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(f"Saved {len(rows)} mock runs; model=none")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    args = parser.parse_args()
    with asyncio.Runner(
        loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else asyncio.new_event_loop,
    ) as runner:
        runner.run(build(args.database_url))
