"""Named async steps with redacted, durable per-check checkpoints.

Check modules expose get_steps() -> list[Step]. A step receives CheckContext,
and returns a JSON-compatible dict of results or None. Checkpoint request keys
with ctx.checkpoint(...) BEFORE any money-moving experiment so retries can
reuse them after an interruption. ctx.load_check_data("D1") reads earlier checks.
"""

import asyncio
import json
import re
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import httpx

from .client import SandboxClient
from .env import SandboxSettings
from .manual import ManualSteps
from .poller import BalancePoller
from .recorder import Recorder, timestamp, utc_now
from .redact import Redactor

REPLACE_ATTEMPTS = 40
REPLACE_PAUSE_SECONDS = 0.05


class CheckFailed(RuntimeError):
    """A check stopped; messages contain names and exception types only."""


@dataclass(frozen=True)
class Step:
    name: str
    run: Callable[["CheckContext"], Awaitable[dict | None]]

    def __post_init__(self):
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", self.name):
            raise ValueError("Step names must be lowercase identifiers.")


def load_check_data(root: str | Path, check_id: str) -> dict:
    """Load the newest saved check data, not credentials or raw recordings."""
    if not re.fullmatch(r"D(?:[1-9]|1[0-3])", check_id):
        raise ValueError("Check ID must be D1 through D13.")
    candidates = sorted(Path(root).glob(f"????-??-??/{check_id}/state.json"))
    if not candidates:
        return {}
    state = json.loads(candidates[-1].read_text(encoding="utf-8"))
    if not isinstance(state, dict) or not isinstance(state.get("data"), dict):
        raise CheckFailed("Saved check data has an invalid format.")
    return state["data"]


class CheckContext:
    def __init__(
        self,
        *,
        client,
        recorder,
        manual,
        state,
        state_path,
        clock=utc_now,
        output=print,
        sleep=asyncio.sleep,
    ):
        self.client: SandboxClient = client
        self.recorder: Recorder = recorder
        self.manual: ManualSteps = manual
        self.root = recorder.root
        self.state = state
        self.state_path: Path = state_path
        self.check_dir = state_path.parent
        self.clock = clock
        self.sleep = sleep
        self._output = output

    @property
    def data(self) -> dict:
        return self.state["data"]

    def note(self, message: str):
        self._output(self.recorder.redactor.text(message))

    def write_json(self, path: str | Path, value):
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        content = (
            json.dumps(
                self.recorder.redactor.redact(value), ensure_ascii=False, indent=2, allow_nan=False
            )
            + "\n"
        )
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        temporary.write_text(content, encoding="utf-8")
        # Windows refuses the replace while another check (D11, D12) is reading the file.
        for attempt in range(REPLACE_ATTEMPTS):
            try:
                temporary.replace(destination)
                return
            except PermissionError:
                if attempt == REPLACE_ATTEMPTS - 1:
                    raise
                time.sleep(REPLACE_PAUSE_SECONDS)

    def checkpoint(self, **updates):
        """Persist JSON data immediately; call before writes or lengthy manual handoffs."""
        self.data.update(updates)
        self.state["updated_at"] = timestamp(self.clock())
        self.write_json(self.state_path, self.state)

    def load_check_data(self, check_id: str) -> dict:
        return load_check_data(self.root, check_id)


class Runner:
    def __init__(
        self,
        settings: SandboxSettings,
        *,
        root: str | Path = "recordings/sandbox",
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], datetime] = utc_now,
        output: Callable[[str], None] = print,
        manual: ManualSteps | None = None,
        poll_interval: float = 300,
        sleep=asyncio.sleep,
    ):
        self.settings = settings
        self.root = Path(root)
        self.transport = transport
        self.clock = clock
        self.output = output
        self.redactor = Redactor.from_settings(settings)
        self.manual = manual if manual is not None else ManualSteps(self.redactor, output_fn=output)
        self.poll_interval = poll_interval
        self.sleep = sleep

    async def run(
        self,
        check_id: str,
        *,
        steps: Sequence[Step] | None = None,
        fresh: bool = False,
        run_date: date | None = None,
    ) -> dict:
        from .checks import get_steps

        recorder = Recorder(check_id, root=self.root, redactor=self.redactor, clock=self.clock)
        selected = list(get_steps(check_id) if steps is None else steps)
        names = [step.name for step in selected]
        if not selected or len(set(names)) != len(names):
            raise CheckFailed("A check must have distinct, nonempty steps.")
        if check_id != "D1" and load_check_data(self.root, "D1").get("verdict") != "CONFIRMED":
            raise CheckFailed("D1 must be CONFIRMED before running other sandbox checks.")
        run_date = run_date or self.clock().astimezone(UTC).date()
        state_path = self.root / run_date.isoformat() / check_id / "state.json"
        if state_path.exists() and not fresh:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        else:
            state = {
                "schema_version": 1,
                "check_id": check_id,
                "completed_steps": [],
                "data": {},
                "in_progress": None,
                "started_at": timestamp(self.clock()),
            }
        if (
            not isinstance(state, dict)
            or state.get("schema_version") != 1
            or state.get("check_id") != check_id
            or not isinstance(state.get("data"), dict)
            or not isinstance(state.get("completed_steps"), list)
            or state["completed_steps"] != names[: len(state["completed_steps"])]
            or len(state["completed_steps"]) > len(names)
        ):
            raise CheckFailed("Saved runner state does not match this check's step definition.")
        if state["completed_steps"] == names:
            self.output(f"{check_id} already complete; use --fresh to run it again.")
            return self.redactor.redact(state)
        async with SandboxClient(
            self.settings, recorder, transport=self.transport, clock=self.clock
        ) as client:
            ctx = CheckContext(
                client=client,
                recorder=recorder,
                manual=self.manual,
                state=state,
                state_path=state_path,
                clock=self.clock,
                output=self.output,
                sleep=self.sleep,
            )
            ctx.checkpoint()
            stop = asyncio.Event()
            poller = BalancePoller(
                client, interval=self.poll_interval, clock=self.clock, output=ctx.note
            )
            poll_task = asyncio.create_task(poller.run(stop))
            try:
                for step in selected[len(state["completed_steps"]) :]:
                    state["in_progress"] = step.name
                    state.pop("last_error", None)
                    ctx.checkpoint()
                    ctx.note(f"{check_id}: {step.name}")
                    try:
                        result = await step.run(ctx)
                        if result is not None:
                            if not isinstance(result, dict):
                                raise TypeError("Step results must be a dict or None.")
                            ctx.data.update(result)
                        state["completed_steps"].append(step.name)
                        state["in_progress"] = None
                        ctx.checkpoint()
                    except Exception as error:  # noqa: BLE001 - Persist progress without exposing callback errors.
                        state["last_error"] = type(error).__name__
                        ctx.checkpoint()
                        raise CheckFailed(
                            f"{check_id} stopped at {step.name} ({type(error).__name__}); "
                            "progress is saved."
                        ) from None
            finally:
                stop.set()
                await poll_task
        return self.redactor.redact(state)
