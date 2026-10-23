"""Local receiver for real sandbox webhooks (S3). Stores, never verifies or acts.

Run: uvicorn scripts.sandbox_checks.catcher:app --port 8000
Layout under the recordings root (default recordings/sandbox):
- webhooks/<name>.json        public: redacted headers and body, received_at, sha256
- raw/webhooks/<name>.body    exact request bytes, gitignored, for D11 signature checks
- raw/webhooks/<name>.headers.json  original headers, gitignored
"""

import hashlib
import json
import re
import threading
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, Request, Response

from .env import load_settings
from .recorder import timestamp, utc_now
from .redact import Redactor

WEBHOOK_PATH = "/webhooks/paypal"


class WebhookStore:
    def __init__(
        self,
        *,
        redactor: Redactor,
        root: str | Path = "recordings/sandbox",
        clock: Callable[[], datetime] = utc_now,
    ):
        self.redactor = redactor
        self.root = Path(root)
        self.public_dir = self.root / "webhooks"
        self.raw_dir = self.root / "raw" / "webhooks"
        self._clock = clock
        self._lock = threading.Lock()

    def store(self, raw_body: bytes, headers: dict[str, str]) -> Path:
        received = self._clock()
        try:
            payload = json.loads(raw_body)
        except (ValueError, UnicodeDecodeError):
            payload = None
        event = payload if isinstance(payload, dict) else {}
        event_id = str(event.get("id") or "no-id")
        slug = re.sub(r"[^A-Za-z0-9_-]+", "_", event_id).strip("_")[:60] or "no-id"
        stamp = received.astimezone(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        self.public_dir.mkdir(parents=True, exist_ok=True)
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        with self._lock:
            # Duplicate deliveries of one event id each get their own sequence number.
            sequence = 1
            while True:
                name = f"{stamp}_{sequence:03d}_{slug}"
                try:
                    with (self.raw_dir / f"{name}.body").open("xb") as handle:
                        handle.write(raw_body)
                    break
                except FileExistsError:
                    sequence += 1
        with (self.raw_dir / f"{name}.headers.json").open("x", encoding="utf-8") as handle:
            json.dump(headers, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        public = {
            "received_at": timestamp(received),
            "event_id": event.get("id"),
            "event_type": event.get("event_type"),
            "resource_type": event.get("resource_type"),
            "create_time": event.get("create_time"),
            "sha256": hashlib.sha256(raw_body).hexdigest(),
            "size": len(raw_body),
            "raw_file": f"raw/webhooks/{name}.body",
            "headers": headers,
            "body": payload if payload is not None else raw_body,
        }
        destination = self.public_dir / f"{name}.json"
        destination.write_text(
            json.dumps(self.redactor.redact(public), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return destination


def create_app(
    store: WebhookStore | None = None,
    *,
    root: str | Path = "recordings/sandbox",
    env_path: str | Path = ".env",
) -> FastAPI:
    """Without a store, settings load at startup (never at import) to build the redactor."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if getattr(app.state, "store", None) is None:
            settings = load_settings(env_path)
            app.state.store = WebhookStore(redactor=Redactor.from_settings(settings), root=root)
        yield

    app = FastAPI(title="CounterSign sandbox webhook catcher", lifespan=lifespan)
    app.state.store = store

    @app.post(WEBHOOK_PATH)
    async def receive(request: Request) -> Response:
        raw_body = await request.body()
        headers = {key.lower(): value for key, value in request.headers.items()}
        try:
            request.app.state.store.store(raw_body, headers)
        except OSError:
            # Not stored: a non-2xx makes PayPal redeliver.
            return Response(status_code=500)
        return Response(status_code=200)

    return app


app = create_app()
