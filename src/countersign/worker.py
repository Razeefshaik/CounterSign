"""Coordinate existing services; only the executor owns PayPal writes.

An explicit worker_once runs every stage once. Background loops have independent,
conservative intervals and never overlap another invocation of their own stage.
"""

import asyncio
import logging
import math
import os
from dataclasses import dataclass

from countersign.shield.snapshot import PAYING

logger = logging.getLogger(__name__)


class WorkerSettingsError(ValueError):
    """Fixed configuration errors that never include environment values."""


@dataclass(frozen=True)
class WorkerSettings:
    inbox_interval: float = 5
    reconcile_interval: float = 60
    executor_interval: float = 60
    max_proposals: int = 100

    def __post_init__(self):
        for interval in (self.inbox_interval, self.reconcile_interval, self.executor_interval):
            if type(interval) not in (int, float) or not math.isfinite(interval) or interval <= 0:
                raise WorkerSettingsError("Worker intervals must be positive finite seconds")
        if type(self.max_proposals) is not int or not 1 <= self.max_proposals <= 1000:
            raise WorkerSettingsError("Worker proposal limit must be an integer from 1 to 1000")


def load_worker_settings() -> WorkerSettings:
    """Read process settings at startup only; never open or read .env."""
    try:
        return WorkerSettings(
            inbox_interval=float(os.environ.get("COUNTERSIGN_INBOX_INTERVAL_SECONDS", "5")),
            reconcile_interval=float(
                os.environ.get("COUNTERSIGN_RECONCILE_INTERVAL_SECONDS", "60")
            ),
            executor_interval=float(os.environ.get("COUNTERSIGN_EXECUTOR_INTERVAL_SECONDS", "60")),
            max_proposals=int(os.environ.get("COUNTERSIGN_WORKER_MAX_PROPOSALS", "100")),
        )
    except (TypeError, ValueError, OverflowError):
        raise WorkerSettingsError("Invalid worker configuration") from None


async def _terminal_count(database) -> int:
    async with database.tx() as conn:
        cursor = await conn.execute(
            "SELECT count(*) AS count FROM webhook_inbox "
            "WHERE status IN ('PROCESSED','IGNORED','REJECTED')"
        )
        return (await cursor.fetchone())["count"]


async def _process_inbox(database, inbox) -> dict:
    before = await _terminal_count(database)
    await inbox.process_one()
    after = await _terminal_count(database)
    # process_one can return True for UNKNOWN or ERROR; those are not completed.
    # A sibling process may finish another event concurrently: one pass handles at most one.
    return {"processed_webhooks": min(1, max(0, after - before))}


async def execute_pending(database, executor, *, limit: int = 100) -> dict:
    """Route ready proposals, counting only newly created operation records.

    Selecting a proposal does not grant permission: execute() locks, rechecks and
    owns all financial state changes. PAYING is the same capability set checked
    by the current executor. Other actions retain their state and approval;
    their one-time deferral audit never consumes a supported proposal's slot.
    """
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise WorkerSettingsError("Worker proposal limit must be an integer from 1 to 1000")
    async with database.tx() as conn:
        # Serialize the audit existence check so parallel workers cannot record
        # the same deferral twice. This lock grants no financial permission.
        await conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended('countersign:worker-deferrals',0))"
        )
        actions = sorted(str(action) for action in PAYING)
        await conn.execute(
            "INSERT INTO audit_log(actor,kind,proposal_id,details) "
            "SELECT 'system','STATE_TRANSITION',p.id,"
            "jsonb_build_object('reason','EXECUTION_DEFERRED','action',p.action) "
            "FROM proposals p WHERE p.state IN ('ALLOWED','APPROVED') "
            "AND NOT (p.action=ANY(%s)) AND NOT EXISTS "
            "(SELECT 1 FROM audit_log a WHERE a.proposal_id=p.id AND a.actor='system' "
            "AND a.kind='STATE_TRANSITION' AND a.details->>'reason'='EXECUTION_DEFERRED')",
            (actions,),
        )
        cursor = await conn.execute(
            "SELECT id FROM proposals WHERE state IN ('ALLOWED','APPROVED') "
            "AND action=ANY(%s) "
            "ORDER BY created_at,id LIMIT %s",
            (actions, limit),
        )
        identifiers = [row["id"] for row in await cursor.fetchall()]
    result = {"proposals": 0, "operations": 0, "errors": 0}
    observed_operations = set()
    for identifier in identifiers:
        try:
            before = await executor.status(identifier)
            await executor.execute(identifier)
            after = await executor.status(identifier)
        except Exception:  # noqa: BLE001 - refusal remains owned by executor, no private error text.
            result["errors"] += 1
        else:
            result["proposals"] += 1
            observed_operations.update(set(after["operations"]) - set(before["operations"]))
    result["operations"] = len(observed_operations)
    return result


async def _execute_and_sweep(database, executor, *, limit):
    try:
        result = await execute_pending(database, executor, limit=limit)
    except Exception:  # noqa: BLE001 - a queue read failure must not prevent UNKNOWN recovery.
        result = {"proposals": 0, "operations": 0, "errors": 1}
    try:
        await executor.sweep()
    except Exception:  # noqa: BLE001 - count failures; do not log private errors or retry writes here.
        result["errors"] += 1
    return result


async def worker_once(database, inbox, reconciler, executor, *, limit: int = 100, commander=None) -> dict:
    """One explicit facade tick, using only the public service boundaries.

    proposals counts ready proposals successfully routed to execute(); operations
    counts new operation IDs, never replay calls or status transitions. errors is
    a count, so private exception values never enter the facade response. An
    optional commander callback investigates once after reconciliation and before
    routing ready proposals; it grants no execution permission of its own.
    """
    result = {"processed_webhooks": 0, "proposals": 0, "operations": 0, "errors": 0}
    try:
        result.update(await _process_inbox(database, inbox))
    except Exception:  # noqa: BLE001 - every stage still gets a chance to progress safely.
        result["errors"] += 1
    try:
        observed = await reconciler.poll_once()
        result["errors"] += sum(
            getattr(observed, stage).errors
            for stage in ("attempts", "disputes", "balances", "backfill")
        )
    except Exception:  # noqa: BLE001 - safe counts only; reconciler retains pending resources.
        result["errors"] += 1
    if commander is not None:
        try:
            await commander()
        except Exception:  # noqa: BLE001 - private model/tool errors become a safe count only.
            result["errors"] += 1
    try:
        executed = await _execute_and_sweep(database, executor, limit=limit)
        for key in ("proposals", "operations", "errors"):
            result[key] += executed[key]
    except Exception:  # noqa: BLE001 - storage failure before routing remains retryable next pass.
        result["errors"] += 1
    return result


async def _periodic(callback, stop, interval, failure_message):
    while not stop.is_set():
        try:
            await callback()
        except Exception:  # noqa: BLE001 - fixed message only, no private traceback.
            logger.warning(failure_message)
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            continue


async def run_workers(database, inbox, reconciler, executor, stop: asyncio.Event, *, settings=None):
    """Run inbox, reconciliation and executor maintenance until shutdown.

    Shutdown cancels pending I/O before the application closes owned connections.
    Cancellation during a write leaves recovery to the executor's durable state.
    """
    configured = settings if settings is not None else WorkerSettings()
    if stop.is_set():
        return
    tasks = [
        asyncio.create_task(
            _periodic(
                inbox.process_one,
                stop,
                configured.inbox_interval,
                "Webhook worker unavailable; durable work remains pending",
            )
        ),
        asyncio.create_task(
            _periodic(
                reconciler.poll_once,
                stop,
                configured.reconcile_interval,
                "Reconciliation unavailable; next cycle will retry",
            )
        ),
        asyncio.create_task(
            _periodic(
                lambda: _execute_and_sweep(database, executor, limit=configured.max_proposals),
                stop,
                configured.executor_interval,
                "Executor maintenance unavailable; durable work remains pending",
            )
        ),
    ]
    try:
        await stop.wait()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def run_inbox(inbox, stop: asyncio.Event, *, interval: float = 5.0):
    while not stop.is_set():
        try:
            await inbox.process_one()
        except Exception:  # noqa: BLE001 - retain pending work without private exception text.
            logger.warning("Webhook worker unavailable; durable work remains pending")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            pass
