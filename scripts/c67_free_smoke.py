"""The owner-approved free Gemma smoke, sharing the 2500-request run budget."""

import asyncio
import json
import sys
from datetime import UTC, datetime

from countersign.agent.openrouter import ACTIVE_BUDGET
from scripts.c67_budget import Budget
from scripts.c67_secrets import openrouter_config
from scripts.c67_smoke import EVIDENCE, ROOT
from scripts.llm_smoke import _public_code, smoke

MODEL = "google/gemma-4-31b-it:free"


def main():
    path = EVIDENCE / "gemma-smoke.json"
    if path.exists():
        print("C67_SMOKE_ALREADY_RECORDED")
        return 1
    budget = Budget(EVIDENCE / "free-budget.json", dollars="0", requests=2500, model=MODEL)
    token = ACTIVE_BUDGET.set(budget)
    result = {"at": datetime.now(UTC).isoformat(), "label": "Gemma via OpenRouter",
              "model": MODEL, "status": "started", "ground": "local harmless read stub"}
    path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    try:
        config = openrouter_config(ROOT.parent / "CounterSign/.env", model=MODEL)
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop
                            if sys.platform == "win32" else None) as runner:
            result.update(runner.run(smoke(config, round_trip=True)))
        result["status"] = "passed"
    except Exception as error:  # noqa: BLE001 - provider errors are private.
        result.update(status="failed", error=_public_code(error))
    finally:
        ACTIVE_BUDGET.reset(token)
        result.update(http_requests=len(budget.state["requests"]),
                      ceiling_charge_usd=str(budget.charged), request_cap=2500)
        path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
