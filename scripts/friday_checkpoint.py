"""C62: image-only Friday capture/boot. Never load an environment file."""

import argparse
import asyncio
import hashlib
import json
import os
import re
import shutil
import time
from pathlib import Path

import httpx

from countersign.api.friday_data import Bundle, FridayError, capture_database, validate_database
from countersign.config import load_settings
from countersign.db import Database


async def mock(client, action):
    response = await client.post("http://127.0.0.1:8099/_mock/checkpoints/friday/" + action)
    if response.status_code != 200:
        raise FridayError("FRIDAY_MOCK_FAILED")
    return response.json()


async def build(directory, app_url, *, pause=asyncio.sleep):
    root = Path(os.environ["COUNTERSIGN_DEMO_ROOT"])
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    if (directory / "manifest.json").exists():
        raise FridayError("FRIDAY_ALREADY_BUILT")
    async with httpx.AsyncClient(timeout=30, trust_env=False, follow_redirects=False) as client:
        for index in range(1, 7):
            response = await client.post(app_url + "/api/v1/demo/step")
            if response.status_code != 202:
                raise FridayError("FRIDAY_BUILD_STEP_REFUSED")
            deadline = time.monotonic() + 1800
            while True:
                response = await client.get(app_url + "/api/v1/demo/state")
                response.raise_for_status()
                state = response.json()
                if not state["running"]:
                    if state["step"] != index or state.get("last_result", {}).get("ok") is not True:
                        raise FridayError("FRIDAY_BUILD_STEP_FAILED")
                    break
                if time.monotonic() > deadline:
                    raise FridayError("FRIDAY_BUILD_TIMEOUT")
                await pause(0.1)
        result = await mock(client, "save")
        settings = load_settings()
        async with Database(settings) as database, database.tx() as conn:
            data = await capture_database(conn)
        waiting = validate_database(data)
        (directory / "database.json").write_text(json.dumps(data), encoding="utf-8")
        shutil.copytree(root, directory / "week")
        dates = sorted(p.name for p in (directory / "week").iterdir()
                       if p.is_dir() and re.fullmatch(r"\d{4}-\d{2}-\d{2}", p.name))
        if len(dates) != 1:
            raise FridayError("FRIDAY_WEEK_INVALID")
        files = {
            path.relative_to(directory).as_posix():hashlib.sha256(path.read_bytes()).hexdigest()
            for path in directory.rglob("*") if path.is_file()
        }
        manifest = {"format":1, "checkpoint":"friday", "date":dates[0],
                    "proposal_id":waiting["id"], "webhook_id":result["webhook_id"], "files":files}
        (directory / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        Bundle(directory).load()


async def boot(directory, app_url):
    bundle = Bundle(directory)
    manifest, _database = bundle.load()
    async with httpx.AsyncClient(timeout=30, trust_env=False, follow_redirects=False) as client:
        result = await mock(client, "load")
    identifier = result.get("webhook_id")
    if identifier != manifest["webhook_id"] or not re.fullmatch(r"WH-MOCK-[A-Za-z0-9_-]{1,100}", identifier or ""):
        raise FridayError("FRIDAY_WEBHOOK_MISMATCH")
    # Child-local setup only; the shell captures this ID into its own environment.
    os.environ["PAYPAL_WEBHOOK_ID"] = identifier
    bundle.week(Path(os.environ["COUNTERSIGN_DEMO_ROOT"]), load_settings(), app_url)
    print(identifier)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("build", "boot"))
    parser.add_argument("--directory", required=True)
    parser.add_argument("--app-url", required=True)
    args = parser.parse_args(argv)
    try:
        settings = load_settings()
        if settings.mode != "demo" or not settings.paypal.is_mock:
            raise FridayError("FRIDAY_MOCK_ONLY")
        asyncio.run((build if args.action == "build" else boot)(args.directory, args.app_url))
    except Exception:  # noqa: BLE001 - never echo settings, file or provider diagnostics.
        print("FRIDAY_CHECKPOINT_FAILED")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
