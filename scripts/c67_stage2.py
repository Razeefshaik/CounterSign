"""One authorized mock week, isolated process settings and an empty env file."""

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
import psycopg
from alembic import command as migrations
from alembic.config import Config

from countersign.agent.codex_cli import CLI_VERSION, child_environment
from scripts.c67_week_recording import MAX_TURNS
from scripts.mock_rehearsal import prepare

ROOT = Path(__file__).resolve().parents[1]
RECORD_ROOT = ROOT / "recordings/real_model/2026-11-04-c67-cli-week-fix"
DATABASE = "postgresql://postgres@127.0.0.1:55433/countersign_c67_demo"
APP = "http://127.0.0.1:8201"
MOCK = "http://127.0.0.1:8099"


def environment(source=None):
    # No inherited provider keys, sandbox PayPal values, database or hosted flags.
    return child_environment(source)


def write_metadata(value):
    path = RECORD_ROOT / "run-metadata.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8", newline="\n")
    temporary.replace(path)


def start(args, env):
    return subprocess.Popen([sys.executable, *args], cwd=ROOT, env=env,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)


def main():
    if (RECORD_ROOT / "run-metadata.json").exists():
        raise RuntimeError("C67_WEEK_ALREADY_ATTEMPTED")
    preflight = json.loads((RECORD_ROOT / "offline-rehearsal.json").read_text())
    if preflight["status"] != "passed" or preflight["model_calls"] != 0:
        raise RuntimeError("C67_OFFLINE_REHEARSAL_REQUIRED")
    if json.loads((RECORD_ROOT / "preflight.json").read_text())["status"] != "passed":
        raise RuntimeError("C67_LIVE_SCHEMA_PREFLIGHT_REQUIRED")
    for port in (8099, 8201):
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                raise RuntimeError("C67_SERVICE_PORT_IN_USE")
    with psycopg.connect(DATABASE) as conn:
        if conn.info.dbname != "countersign_c67_demo" or conn.info.server_version // 10000 != 16:
            raise RuntimeError("C67_DATABASE_REFUSED")
    config = Config(str(ROOT / "migrations/alembic.ini"))
    config.attributes["database_url"] = DATABASE
    migrations.upgrade(config, "head")
    accepted = json.loads((ROOT / "recordings/real_model/2026-11-04-c67-cli-stage1-rehearsed/run.json").read_text())
    if accepted["status"] != "passed":
        raise RuntimeError("C67_ACCEPTED_STAGE_ONE_REQUIRED")
    services = {}
    mock_env = environment() | {"MOCK_CONTROL": "1"}
    def start_mock():
        services["mock"] = start(["-m", "mock_paypal"], mock_env)
        return services["mock"]
    with httpx.Client(base_url=MOCK, timeout=10, trust_env=False) as client:
        setup = prepare(client, app_port=8201, start_mock=start_mock)
    base = environment() | {"DATABASE_URL": DATABASE, "COUNTERSIGN_MODE": "demo",
        "PAYPAL_BASE_URL": MOCK, "PAYPAL_WEBHOOK_ID": setup["webhook_id"],
        "MOCK_PAYPAL_CLIENT_ID": "mock-client", "MOCK_PAYPAL_CLIENT_SECRET": "mock-secret",
        "OWNER_TOKEN": "c67-local-mock-owner"}
    date = datetime.now(UTC).date().isoformat()
    with tempfile.TemporaryDirectory(prefix="c67-week-empty-env-") as folder:
        empty = Path(folder) / "empty.env"
        empty.write_bytes(b"")
        services["receiver"] = start(["-m", "scripts.demo_week", "--target", "mock", "--serve",
            "--port", "8201", "--env-file", str(empty)], base)
        for _ in range(150):
            if services["receiver"].poll() is not None:
                raise RuntimeError("C67_RECEIVER_START_FAILED")
            try:
                response = httpx.get(APP + "/api/v1/meta", timeout=1, trust_env=False)
                if response.status_code == 200 and response.json().get("paypal_env") == "mock":
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        else:
            raise RuntimeError("C67_RECEIVER_NOT_READY")
        live = base | {"COUNTERSIGN_LLM_PROVIDER": "codex_cli", "COUNTERSIGN_LLM_MODEL": accepted["model"],
            "COUNTERSIGN_TOOLKIT_PYTHON": str((ROOT.parent / "CounterSign/.venv/toolkit/Scripts/python.exe").resolve())}
        assert not any(key in live for key in ("PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET", "GOOGLE_API_KEY",
                                               "OPEN_ROUTER_API_KEY", "OPENAI_API_KEY"))
        metadata = {"stage": 2, "status": "started", "date": datetime.now(UTC).isoformat(),
            "provider": "codex_cli", "model": accepted["model"], "cli_version": CLI_VERSION,
            "cli_adds_own_system_instructions": True, "model_turn_cap": MAX_TURNS, "retries": 0,
            "retry_scope": "application never reruns a turn; accepted stage-one CLI handling unchanged",
            "previous_week": "recordings/real_model/2026-11-04-c67-cli-week/",
            "environment_file": "new empty temporary file", "main_environment_file_used": False,
            "sandbox_paypal_credentials_loaded": False, "mock_credentials_only": True,
            "database_name": "countersign_c67_demo", "database_port": 55433,
            "mock_url": MOCK, "app_url": APP, "services": {name: proc.pid for name, proc in services.items()},
            "record_directory": date, "stage_three_started": False}
        write_metadata(metadata)  # A second launcher invocation is forbidden from this point.
        args = [sys.executable, "-m", "scripts.demo_week", "--target", "mock", "--model", "configured",
            "--record-model-decisions", "--env-file", str(empty), "--app-url", APP,
            "--root", str(RECORD_ROOT), "--date", date]
        started = time.monotonic()
        result = subprocess.run(args, cwd=ROOT, env=live, capture_output=True, check=False)
        metadata.update(status="completed" if result.returncode == 0 else "stopped", exit_code=result.returncode,
                        wall_seconds=round(time.monotonic() - started, 3))
        # Raw diagnostics are never exported; the checkpoint and turn records contain fixed codes.
        write_metadata(metadata)
        print(json.dumps({"stage": 2, "status": metadata["status"], "exit_code": result.returncode,
                          "wall_seconds": metadata["wall_seconds"]}))
        return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
