"""Five-minute balance observations, independent of a check's success verdict."""

import asyncio
import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from .client import SandboxClient, SandboxRequestError, body_value
from .recorder import timestamp, utc_now


class BalancePoller:
    def __init__(
        self,
        client: SandboxClient,
        *,
        path: str | Path | None = None,
        interval: float = 300,
        clock: Callable[[], datetime] = utc_now,
        output: Callable[[str], None] = print,
    ):
        if interval <= 0:
            raise ValueError("Balance polling interval must be positive.")
        self.client = client
        self.path = Path(path) if path is not None else client.recorder.root / "balances.jsonl"
        self.interval = interval
        self.clock = clock
        self.output = output

    async def poll_once(self) -> dict:
        observation = {"fetched_at": timestamp(self.clock())}
        try:
            response = await self.client.request(
                "GET", "/v1/reporting/balances", step="balance_snapshot"
            )
            observation.update(
                response_status=response.status_code,
                response_body=body_value(
                    response.content, response.headers.get("content-type", "")
                ),
            )
        except SandboxRequestError as error:
            # Do not copy exception text: transports may embed private request data.
            observation.update(response_status=None, error=type(error).__name__)
        public = self.client.redactor.redact(observation)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(public, ensure_ascii=False, allow_nan=False) + "\n")
        if public.get("error") or public.get("response_status") != 200:
            self.output("Balance observation unavailable; see redacted balances.jsonl.")
        return public

    async def run(self, stop: asyncio.Event):
        # Always collect one observation, including for short checks.
        while True:
            await self.poll_once()
            if stop.is_set():
                return
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.interval)
                return
            except TimeoutError:
                pass
