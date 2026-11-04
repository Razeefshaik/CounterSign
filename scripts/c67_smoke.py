"""Owner-approved C67 smoke only: $0.25, six HTTP requests, no PayPal access."""

import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from countersign.agent.openrouter import ACTIVE_BUDGET
from scripts.c67_budget import Budget
from scripts.llm_smoke import _public_code

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "design-demos/reference/c67"


def main():
    from scripts.c67_secrets import openrouter_config
    from scripts.llm_smoke import smoke

    EVIDENCE.mkdir(parents=True, exist_ok=True)
    result_path = EVIDENCE / "smoke.json"
    if result_path.exists():
        print("C67_SMOKE_ALREADY_RECORDED: preserve the outcome; no automatic rerun")
        return 1
    budget = Budget(EVIDENCE / "smoke-budget.json", dollars="0.25", requests=6)
    token = ACTIVE_BUDGET.set(budget)
    result = {"at": datetime.now(UTC).isoformat(), "provider": "openrouter",
              "label": "Gemini via OpenRouter", "model": "google/gemini-3.5-flash",
              "scope": "harmless local read stub; no PayPal or database", "status": "started"}
    result_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    try:
        # Reads only this one variable at runtime; never exports to shell/environment.
        config = openrouter_config(ROOT.parent / "CounterSign/.env")
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop
                            if sys.platform == "win32" else None) as runner:
            result.update(runner.run(smoke(config, round_trip=True)))
        result["status"] = "passed"
    except Exception as error:  # noqa: BLE001 - never copy provider/key/file exception values.
        result.update(status="failed", error=_public_code(error))
    finally:
        ACTIVE_BUDGET.reset(token)
        result.update(http_requests=len(budget.state["requests"]),
                      ceiling_charge_usd=str(budget.charged),
                      budget_usd="0.25", request_cap=6)
        result_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
