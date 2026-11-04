"""One five-scenario pilot, unchanged systems/metrics/prompts and durable cells."""

import argparse
import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

from countersign.agent.models import ChatModelConfig, LangChainModel, ModelError
from eval.evaluate import atomic_json, disposable_database, evaluate, load_scenarios
from eval.runner import ModelQuotaExhausted, run_one
from scripts.c67_week_recording import public
from scripts.sandbox_checks.redact import Redactor

ROOT = Path(__file__).resolve().parents[1]
RECORD_ROOT = ROOT / "recordings/real_model/2026-11-04-c67-cli-week-fix"
ADMIN = "postgresql://postgres@127.0.0.1:55433/countersign_c67_test"
CRITICAL = {"MODEL_NATIVE_TOOL_FORBIDDEN", "MODEL_CANARY_DISCLOSED", "MODEL_QUOTA_EXHAUSTED"}


class PilotStopped(RuntimeError):
    pass


class OfflineModel:
    model_id = "offline-rehearsal"

    def __init__(self):
        self.last_usage_metadata = {}

    async def complete(self, request):
        self.last_usage_metadata = {"input_tokens": 0, "output_tokens": 0}
        if any(t["function"]["name"] == "get_balances" for t in request["tools"]):
            # Reproduce the real CLI's existing baseline binding guard, without a model.
            raise ModelError("MODEL_TOOL_NOT_ALLOWED")
        return {"incident_id": request["data"]["incident"]["id"], "action": "WAIT", "params": {},
                "evidence_refs": [], "rationale": "Offline pilot rehearsal; no model backend.",
                "expected_effect": {"available_change": {"minor": 0, "currency": "USD"}}}


async def run_pilot(*, offline, output):
    scenarios = load_scenarios()[:5]
    accepted = json.loads((ROOT / "recordings/real_model/2026-11-04-c67-cli-stage1-rehearsed/run.json").read_text())
    identity = "offline-rehearsal" if offline else accepted["model"]
    turns, cells = [], []
    state = {"stop": None}
    redactor = Redactor()

    def save_turns():
        if not offline:
            path = RECORD_ROOT / "pilot-turns.jsonl"
            path.write_text("".join(json.dumps(public(t, redactor)) + "\n" for t in turns), encoding="utf-8", newline="\n")

    async def runner(scenario, system, **kwargs):
        if state["stop"]:
            raise PilotStopped(state["stop"])
        cell = {"system": system.name, "scenario_id": scenario["id"], "complete_attempts": 0,
                "cli_turns": 0, "model_failure": None}
        model = None
        if system.name != "rules_only":
            model = OfflineModel() if offline else LangChainModel(ChatModelConfig("codex_cli", identity))
            original = model.complete
            latched = {"code": None}
            async def complete(request):
                if latched["code"]:
                    raise ModelError(latched["code"])
                cell["complete_attempts"] += 1
                turn = {"system": system.name, "scenario_id": scenario["id"],
                        "number_in_cell": cell["complete_attempts"], "status": "started", "usage": None}
                turns.append(turn)
                save_turns()
                started = time.monotonic()
                try:
                    answer = await original(request)
                    turn.update(status="passed", usage=getattr(model, "last_usage_metadata", None))
                    return answer
                except ModelError as error:
                    code = str(error)
                    turn.update(status="failed", error=code)
                    latched["code"] = cell["model_failure"] = code
                    if code in CRITICAL or code != "MODEL_TOOL_NOT_ALLOWED":
                        state["stop"] = code
                    raise
                finally:
                    turn["seconds"] = round(time.monotonic() - started, 3)
                    metadata = getattr(getattr(model, "_client", None), "last_event_metadata", {})
                    turn["cli_invoked"] = bool(metadata) if not offline else False
                    cell["cli_turns"] += int(turn["cli_invoked"])
                    turn.update(metadata)
                    save_turns()
            model.complete = complete
        kwargs["llm"] = model
        try:
            row = await run_one(scenario, system, **kwargs)
        finally:
            if model is not None and callable(getattr(model, "aclose", None)):
                await model.aclose()
        cell.update(seconds=row["duration_ms"] / 1000, tokens=row["tokens"],
                    error=row["error"], passed=row["passed"])
        cells.append(cell)
        if not offline:
            atomic_json(RECORD_ROOT / "pilot-cells.json", public(cells, redactor))
        return row

    marker = OfflineModel() if offline else LangChainModel(ChatModelConfig("codex_cli", identity))
    stopped = None
    with disposable_database(ADMIN) as database:
        try:
            rows = await evaluate(scenarios, output=output, k=1, model="configured", database_url=database,
                llm=marker, model_id=identity, provider="scripted-offline" if offline else "codex_cli", runner=runner)
        except (ModelQuotaExhausted, PilotStopped) as error:
            stopped = str(error)
            checkpoint = json.loads((output / "checkpoint.json").read_text())
            rows = checkpoint["rows"]
            (output / "runs.partial.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    if isinstance(marker, LangChainModel):
        await marker.aclose()
    baseline = [c for c in cells if c["system"] == "baseline_llm"]
    result = {"status": "stopped" if stopped or state["stop"] else "completed_with_baseline_configuration_failures",
        "offline": offline, "scenario_ids": [s["id"] for s in scenarios], "k": 1,
        "requested_cells": 15, "completed_cells": len(rows), "stop_code": stopped or state["stop"],
        "baseline_configuration_failures": sum(c["model_failure"] == "MODEL_TOOL_NOT_ALLOWED" for c in baseline),
        "baseline_cli_turns": sum(c["cli_turns"] for c in baseline), "cells": cells,
        "model_calls": 0 if offline else sum(c["cli_turns"] for c in cells),
        "prompts_scenarios_metrics_unchanged": True, "reruns": 0, "full_round_started": False}
    return public(result, redactor)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    os.environ["COUNTERSIGN_TOOLKIT_PYTHON"] = str(ROOT.parent / "CounterSign/.venv/toolkit/Scripts/python.exe")
    if not args.offline:
        rehearsal = json.loads((RECORD_ROOT / "pilot-rehearsal.json").read_text())
        if rehearsal["completed_cells"] != 15 or rehearsal["baseline_configuration_failures"] != 5:
            raise RuntimeError("PILOT_REHEARSAL_REQUIRED")
        if (RECORD_ROOT / "pilot-attempt.json").exists():
            raise RuntimeError("PILOT_ALREADY_ATTEMPTED")
        atomic_json(RECORD_ROOT / "pilot-attempt.json", {"status": "started", "requested_cells": 15, "k": 1})
    with tempfile.TemporaryDirectory(prefix="c67-pilot-offline-") as folder:
        output = Path(folder) / "results" if args.offline else RECORD_ROOT / "pilot"
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None) as runner:
            result = runner.run(run_pilot(offline=args.offline, output=output))
    atomic_json(RECORD_ROOT / ("pilot-rehearsal.json" if args.offline else "pilot-attempt.json"), result)
    print(json.dumps({key: result[key] for key in ("status", "completed_cells", "baseline_configuration_failures", "model_calls", "stop_code")}))
    return 0 if result["completed_cells"] == 15 else 1


if __name__ == "__main__":
    raise SystemExit(main())
