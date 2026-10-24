"""Cycle locking and failure recovery against isolated PostgreSQL 16 schemas."""

import asyncio
from dataclasses import asdict
from datetime import UTC, datetime

import pytest

from countersign.db import Database, Repositories
from countersign.paypal.http import HTTPResponse
from countersign.reconcile import Reconciler, ReconcileSettings, StageResult, poller

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
NOW = datetime(2026, 11, 2, 9, tzinfo=UTC)


class EmptyMarketplace:
    def __init__(self):
        self.calls = []

    async def list_disputes(self, **kwargs):
        self.calls.append("disputes")
        return HTTPResponse(200, {"items": [], "links": []})

    async def get_balances(self):
        self.calls.append("balances")
        return HTTPResponse(
            200,
            {
                "balances": [
                    {
                        "currency": "USD",
                        "available_balance": {"value": "10.00", "currency_code": "USD"},
                    }
                ],
                "as_of_time": "2026-11-02T09:00:00Z",
                "last_refresh_time": "2026-11-02T09:00:00Z",
            },
        )

    async def list_webhook_events(self, **kwargs):
        raise AssertionError("Default backfill must not call PayPal")


async def test_racing_cycles_skip_work_and_identical_snapshot_is_deduplicated(
    test_db_url, monkeypatch
):
    entered, release = asyncio.Event(), asyncio.Event()
    real = poller.reconcile_balances

    async def slow(*args, **kwargs):
        entered.set()
        await release.wait()
        return await real(*args, **kwargs)

    monkeypatch.setattr(poller, "reconcile_balances", slow)
    reader = EmptyMarketplace()
    async with Database(test_db_url, max_size=4) as database:
        service = Reconciler(database, reader, clock=lambda: NOW)
        first = asyncio.create_task(service.poll_once())
        try:
            await asyncio.wait_for(entered.wait(), timeout=10)
            calls = list(reader.calls)
            competing = await service.poll_once()
            assert competing.busy and reader.calls == calls
        finally:
            release.set()
        result = await first
        assert not result.busy and result.balances.updated == 1
        assert (await service.poll_once()).balances.updated == 0
        async with database.tx() as conn:
            assert len(await Repositories(conn).balance_snapshots.find()) == 1


async def test_failed_stage_rolls_back_its_writes_and_keeps_other_stages(test_db_url, monkeypatch):
    async def failed(conn, reader, *, now):
        await Repositories(conn).policies.insert(key="failed_stage", value=True)
        raise ValueError("fake-private-token private@example.test")

    async def successful(conn, reader, *, now):
        await Repositories(conn).policies.insert(key="successful_stage", value=True)
        return StageResult(updated=1)

    monkeypatch.setattr(poller, "reconcile_balances", failed)
    monkeypatch.setattr(poller, "reconcile_attempts", successful)
    async with Database(test_db_url) as database:
        result = await Reconciler(database, EmptyMarketplace(), clock=lambda: NOW).poll_once()
        assert result.balances.errors == 1 and result.balances.incidents == 1
        assert result.attempts.updated == 1
        assert "private" not in str(asdict(result))
        async with database.tx() as conn:
            repos = Repositories(conn)
            assert await repos.policies.get("failed_stage") is None
            assert await repos.policies.get("successful_stage") is not None
            incident = (await repos.incidents.find())[0]
            assert incident["subject"]["reason"] == "STAGE_FAILED"
            assert "private" not in str(await repos.audit_log.find())


async def test_schedule_stops_without_waiting_full_interval():
    stop = asyncio.Event()
    service = Reconciler(None, None, settings=ReconcileSettings(interval_seconds=600))
    count = 0

    async def once():
        nonlocal count
        count += 1
        stop.set()

    service.poll_once = once
    await asyncio.wait_for(service.run(stop), timeout=1)
    assert count == 1
