"""At most three approved harmless turns with the genuine eight read schemas."""

import asyncio
import hashlib
import json
import os
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from countersign.agent.codex_cli import CLI_VERSION, create_client, schema_for
from countersign.agent.codex_cli_diagnostics import CLIFailure, save_failure
from countersign.agent.models import ChatModelConfig, LangChainModel, ModelError
from countersign.agent.reads import custom_tools
from countersign.agent.toolkit import ToolkitGateway
from countersign.agent.tools import ToolRegistry
from countersign.config import PayPalSettings

ROOT = Path(__file__).resolve().parents[1]
RECORD_ROOT = ROOT / "recordings/real_model/2026-11-04-c67-cli-week-fix"
RECORD = RECORD_ROOT / "preflight.json"
LAST_DIAGNOSTIC = None


def save(record):
    RECORD_ROOT.mkdir(parents=True, exist_ok=True)
    temporary = RECORD.with_suffix(".tmp")
    temporary.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8", newline="\n")
    temporary.replace(RECORD)


async def preflight(record):
    global LAST_DIAGNOSTIC
    toolkit_python = ROOT.parent / "CounterSign/.venv/toolkit/Scripts/python.exe"
    async with ToolkitGateway(PayPalSettings("http://127.0.0.1:8099", "mock-client", "mock-secret", "WH-C67-PREFLIGHT"),
                              python_executable=toolkit_python) as gateway:
        tools = ToolRegistry((*gateway.read_tools(), *custom_tools(None, None))).schemas
    (RECORD_ROOT / "read-tool-schemas.json").write_text(json.dumps(tools, indent=2) + "\n", encoding="utf-8")
    record["offered_tools"] = [tool["function"]["name"] for tool in tools]
    config = ChatModelConfig("codex_cli", record["model"])
    client = create_client(config)
    model = LangChainModel(config, factory=lambda _: client)
    turn = {"number": len(record["turns"]) + 1, "status": "started",
            "output_schema_sha256": hashlib.sha256(json.dumps(schema_for(tools), sort_keys=True).encode()).hexdigest()}
    record["turns"].append(turn)
    save(record)
    start = time.monotonic()
    try:
        answer = await model.complete({"system": 'Return proposal_json encoding {"preflight":"ok"}. '
            'This is a harmless schema compatibility check; request no tools.',
            "data": {"preflight": "schema compatibility"}, "observations": [], "tools": tools})
        if "proposal_json" in answer:
            try:
                json.loads(answer["proposal_json"])
            except (ValueError, TypeError) as error:
                failure = CLIFailure("MODEL_OUTPUT_NOT_JSON", str(error))
                client.last_local_error_path = save_failure(failure)
                raise failure from None
        turn.update(status="passed", usage=model.last_usage_metadata, native_tool_events=0)
        record["status"] = "passed"
    except ModelError as error:
        code = str(error)
        turn.update(status="failed", error=code)
        record["status"] = "failed"
        record["security_stop"] = code in {"MODEL_NATIVE_TOOL_FORBIDDEN", "MODEL_CANARY_DISCLOSED"}
        # Only a fixed keyword summary is exported, never the private error text/path.
        if client.last_local_error_path is not None:
            detail = client.last_local_error_path.read_text(encoding="utf-8")
            if re.search(r"not permitted|not supported|unsupported|not allowed", detail, re.IGNORECASE):
                turn["schema_rejection_keywords"] = [name for name in ("pattern", "minimum", "maximum", "title")
                    if re.search(r"['\"]" + name + r"['\"]", detail)]
    finally:
        LAST_DIAGNOSTIC = client.last_local_error_path
        turn.update(client.last_event_metadata)
        turn["seconds"] = round(time.monotonic() - start, 3)
        save(record)
        await model.aclose()
    return record["status"] == "passed"


def main():
    accepted = json.loads((ROOT / "recordings/real_model/2026-11-04-c67-cli-stage1-rehearsed/run.json").read_text())
    if RECORD.exists():
        record = json.loads(RECORD.read_text())
        if record["status"] == "passed" or len(record["turns"]) >= 3 or record.get("security_stop"):
            raise RuntimeError("C67_PREFLIGHT_ALREADY_FINISHED")
        previous = record["turns"][-1]
        if previous.get("provider_error_code") != "invalid_json_schema" or not previous.get("schema_rejection_keywords"):
            raise RuntimeError("C67_PREFLIGHT_STOPPED")
    else:
        record = {"stage": "week preflight", "status": "started", "turn_cap": 3, "turns": [],
            "model": accepted["model"], "provider": "codex_cli", "cli_version": CLI_VERSION,
            "date": datetime.now(UTC).isoformat(), "cli_adds_own_system_instructions": True,
            "exact_stage_one_command": True, "hostile_or_scenario_text": False,
            "previous_week": "recordings/real_model/2026-11-04-c67-cli-week/", "tool_executions": 0}
    os.environ["COUNTERSIGN_LLM_PROVIDER"] = "codex_cli"
    os.environ["COUNTERSIGN_LLM_MODEL"] = accepted["model"]
    save(record)
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None) as runner:
        passed = runner.run(preflight(record))
    last = record["turns"][-1]
    print(json.dumps({"status": record["status"], "turns": len(record["turns"]), "last_turn": last}))
    if LAST_DIAGNOSTIC is not None:
        print("Local diagnostic file: " + str(LAST_DIAGNOSTIC))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
