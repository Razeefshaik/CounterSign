"""Scheduled GET reconciliation, serialized with owner decisions and the inbox."""

import asyncio
import logging
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime

from countersign.fsm.dispute import Phase

from .attempts import reconcile_attempts
from .backfill import backfill_events
from .balances import reconcile_balances
from .common import ReconcileError, StageResult, open_incident
from .disputes import ingest_dispute, reconcile_disputes

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReconcileSettings:
    interval_seconds: float = 60
    backfill_enabled: bool = False  # [pending D11]: never enable by inference.
    outcome_map: Mapping[str, Phase] | None = None  # [pending D10]: verified codes only.

    def __post_init__(self):
        if (
            type(self.interval_seconds) not in (int, float)
            or not math.isfinite(self.interval_seconds)
            or self.interval_seconds <= 0
        ):
            raise ReconcileError("Reconciliation interval must be positive and finite")
        if type(self.backfill_enabled) is not bool:
            raise ReconcileError("Backfill must be explicitly enabled or disabled")


@dataclass
class PollResult:
    busy: bool = False
    attempts: StageResult = field(default_factory=StageResult)
    disputes: StageResult = field(default_factory=StageResult)
    balances: StageResult = field(default_factory=StageResult)
    backfill: StageResult = field(default_factory=StageResult)


class Reconciler:
    def __init__(self, database, reader, *, settings=None, clock=None):
        self.database = database
        self.reader = reader
        self.settings = settings if settings is not None else ReconcileSettings()
        self.clock = clock if clock is not None else lambda: datetime.now(UTC)

    async def _stage(self, conn, name, callback):
        try:
            async with conn.transaction():
                return await callback()
        except Exception:  # noqa: BLE001 - discard the failed stage, never expose driver values.
            created = await open_incident(conn, "poller", name, "STAGE_FAILED")
            return StageResult(errors=1, incidents=int(created))

    async def poll_once(self) -> PollResult:
        """A racing cycle returns busy without GETs or writes; crashes roll back atomically."""
        try:
            async with self.database.tx() as conn:
                lock = await conn.execute(
                    "SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0)) AS locked",
                    ("countersign:owner-decisions",),
                )
                if not (await lock.fetchone())["locked"]:
                    return PollResult(busy=True)
                now = self.clock()
                if not isinstance(now, datetime) or now.utcoffset() is None:
                    raise ReconcileError("Reconciliation clock must be timezone-aware")

                async def on_resource(kind, resource_id, detail):
                    if kind == "dispute":
                        return await ingest_dispute(
                            conn, detail, now=now, outcome_map=self.settings.outcome_map
                        )
                    # Normal attempt polling performs its own identity checks and GET.
                    return StageResult(skipped=1)

                result = PollResult()
                result.backfill = await self._stage(
                    conn,
                    "backfill",
                    lambda: backfill_events(
                        conn,
                        self.reader,
                        now=now,
                        enabled=self.settings.backfill_enabled,
                        on_resource=on_resource,
                    ),
                )
                result.disputes = await self._stage(
                    conn,
                    "disputes",
                    lambda: reconcile_disputes(
                        conn, self.reader, now=now, outcome_map=self.settings.outcome_map
                    ),
                )
                result.attempts = await self._stage(
                    conn, "attempts", lambda: reconcile_attempts(conn, self.reader, now=now)
                )
                result.balances = await self._stage(
                    conn, "balances", lambda: reconcile_balances(conn, self.reader, now=now)
                )
            return result
        except Exception:  # noqa: BLE001 - configuration/connection/commit errors may contain secrets.
            raise ReconcileError("Reconciliation cycle unavailable") from None

    async def run(self, stop: asyncio.Event):
        """Run immediately and then at the configured interval until the caller stops."""
        while not stop.is_set():
            try:
                await self.poll_once()
            except ReconcileError:
                logger.warning("Reconciliation unavailable; next scheduled cycle will retry")
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.settings.interval_seconds)
            except TimeoutError:
                continue
