"""Prepare a local mock rehearsal; never load a key or contact a model/provider.

The database must already be migrated. The helper starts the loopback mock when
absent, registers its webhook, and prints commands for the receiver and week.
An existing mock is preserved unless the owner explicitly supplies --reset.
"""

import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import unquote, urlsplit

import httpx

from countersign.agent.toolkit import child_environment

ROOT = Path(__file__).resolve().parents[1]
MOCK_URL = "http://127.0.0.1:8099"
_IDENTIFIER = re.compile(r"WH-MOCK-[A-Za-z0-9_-]{1,100}\Z")


class RehearsalError(RuntimeError):
    """A fixed diagnostic with no environment, token or upstream response text."""


def database_guard(value):
    """This setup targets only a disposable local database on the fast service."""
    try:
        parsed = urlsplit(value)
        name = unquote(parsed.path.lstrip("/"))
        valid = (
            parsed.scheme in {"postgresql", "postgres"}
            and parsed.hostname in {"127.0.0.1", "localhost"}
            and parsed.port == 55433
            and re.fullmatch(r"[A-Za-z0-9_]+_demo", name)
            and not parsed.fragment
        )
    except (ValueError, TypeError, AttributeError):
        valid = False
    if not valid:
        raise RehearsalError("REHEARSAL_DATABASE_REFUSED")


def _public_text(value):
    if not isinstance(value, str) or not value or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise RehearsalError("REHEARSAL_ARGUMENT_REFUSED")
    return value


def _quote(value):
    """PowerShell literal strings keep $, backticks and apostrophes inert."""
    return "'" + _public_text(str(value)).replace("'", "''") + "'"


def _document(response, status):
    if response.status_code != status:
        raise RehearsalError("REHEARSAL_MOCK_REQUEST_FAILED")
    try:
        body = response.json()
    except ValueError:
        raise RehearsalError("REHEARSAL_MOCK_RESPONSE_INVALID") from None
    if not isinstance(body, dict):
        raise RehearsalError("REHEARSAL_MOCK_RESPONSE_INVALID")
    return body


def _ready(client):
    body = _document(client.get("/_mock/clock"), 200)
    if not isinstance(body.get("now"), str):
        raise RehearsalError("REHEARSAL_MOCK_RESPONSE_INVALID")


def _start_mock():
    environment = child_environment()
    environment["MOCK_CONTROL"] = "1"
    return subprocess.Popen(
        [sys.executable, "-m", "mock_paypal"],
        cwd=ROOT,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


def prepare(client, *, reset=False, app_port=8000, start_mock=None, pause=time.sleep):
    """Use fake mock credentials only; retain a successfully started background mock."""
    if type(app_port) is not int or not 1 <= app_port <= 65535 or app_port == 8099:
        raise RehearsalError("REHEARSAL_RECEIVER_PORT_REFUSED")
    owned = None
    callback = f"http://127.0.0.1:{app_port}/webhooks/paypal"
    try:
        try:
            _ready(client)
        except httpx.ConnectError:
            owned = (start_mock or _start_mock)()
            for _ in range(150):
                if owned.poll() is not None:
                    raise RehearsalError("REHEARSAL_MOCK_START_FAILED")
                try:
                    _ready(client)
                    break
                except httpx.ConnectError:
                    pause(0.1)
            else:
                raise RehearsalError("REHEARSAL_MOCK_START_FAILED")
        if reset or owned is not None:
            _document(client.post("/_mock/reset", json={"seed": 17}), 200)
        token = _document(
            client.post(
                "/v1/oauth2/token",
                auth=("mock-client", "mock-secret"),
                data={"grant_type": "client_credentials"},
            ),
            200,
        ).get("access_token")
        if not isinstance(token, str) or not token:
            raise RehearsalError("REHEARSAL_MOCK_RESPONSE_INVALID")
        headers = {"Authorization": "Bearer " + token}
        registrations = _document(
            client.get("/v1/notifications/webhooks", headers=headers), 200
        ).get("webhooks")
        if not isinstance(registrations, list):
            raise RehearsalError("REHEARSAL_MOCK_RESPONSE_INVALID")
        matching = [
            item
            for item in registrations
            if isinstance(item, dict)
            and item.get("url") == callback
            and item.get("event_types") == [{"name": "*"}]
        ]
        if len(matching) > 1:
            raise RehearsalError("REHEARSAL_DUPLICATE_WEBHOOKS_REVIEW_REQUIRED")
        registration = (
            matching[0]
            if matching
            else _document(
                client.post(
                    "/v1/notifications/webhooks",
                    headers=headers,
                    json={"url": callback, "event_types": [{"name": "*"}]},
                ),
                201,
            )
        )
        identifier = registration.get("id")
        if not isinstance(identifier, str) or not _IDENTIFIER.fullmatch(identifier):
            raise RehearsalError("REHEARSAL_MOCK_RESPONSE_INVALID")
        return {"webhook_id": identifier, "callback": callback, "pid": owned.pid if owned else None}
    except BaseException:
        if owned is not None and owned.poll() is None:
            owned.terminate()
            try:
                owned.wait(timeout=5)
            except subprocess.TimeoutExpired:
                owned.kill()
                owned.wait(timeout=5)
        raise


def print_commands(result, *, env_file, root, app_port=8000):
    """Print no private database URL, file contents, model key or OAuth token."""
    python = _quote(Path(sys.executable))
    toolkit = _quote(ROOT / ".venv" / "toolkit" / "Scripts" / "python.exe")
    if os.name != "nt":
        toolkit = _quote(ROOT / ".venv" / "toolkit" / "bin" / "python")
    print("Mock ready at " + MOCK_URL + "; webhook " + result["webhook_id"])
    if result["pid"] is not None:
        print("Background mock PID: " + str(result["pid"]))
    print("Keep your migrated *_demo DATABASE_URL set; it is deliberately not printed.")
    print("Run these shared settings in both PowerShell terminals:")
    print("Set-Location " + _quote(ROOT))
    print("$env:PYTHONPATH = 'src'")
    print("$env:COUNTERSIGN_MODE = 'demo'")
    print("$env:PAYPAL_BASE_URL = '" + MOCK_URL + "'")
    print("$env:MOCK_PAYPAL_CLIENT_ID = 'mock-client'")
    print("$env:MOCK_PAYPAL_CLIENT_SECRET = 'mock-secret'")
    print("$env:PAYPAL_WEBHOOK_ID = " + _quote(result["webhook_id"]))
    print("$env:COUNTERSIGN_TOOLKIT_PYTHON = " + toolkit)
    common = " --target mock --model configured --env-file " + _quote(env_file)
    print("Terminal 1: receiver, no background workers")
    print("& " + python + " -m scripts.demo_week" + common + f" --serve --port {app_port}")
    print("Terminal 2: real-model rehearsal (owner-operated Google calls)")
    print(
        "& "
        + python
        + " -m scripts.demo_week"
        + common
        + " --root "
        + _quote(root)
        + f" --app-url http://127.0.0.1:{app_port}"
    )
    print("Reuse this root for resume; use --reset only to discard an existing mock deliberately.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, help="Fresh checkpoint root; retain it for resume")
    parser.add_argument("--env-file", default=".env", help="Forwarded to demo; never opened here")
    parser.add_argument("--app-port", type=int, default=8000)
    parser.add_argument(
        "--reset", action="store_true", help="Explicitly discard existing mock state"
    )
    args = parser.parse_args(argv)
    try:
        database_guard(os.environ.get("DATABASE_URL", ""))
        _public_text(args.root)
        _public_text(args.env_file)
        with httpx.Client(
            base_url=MOCK_URL, timeout=5, trust_env=False, follow_redirects=False
        ) as client:
            result = prepare(client, reset=args.reset, app_port=args.app_port)
        print_commands(result, env_file=args.env_file, root=args.root, app_port=args.app_port)
    except KeyboardInterrupt:
        return 130
    except Exception:  # noqa: BLE001 - environment/HTTP/driver failures may carry secrets.
        print("REHEARSAL_SETUP_FAILED: check the local mock and migrated fast *_demo database.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
