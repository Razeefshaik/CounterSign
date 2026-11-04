"""C62: command gating and inactivity use no real services or clocks."""
import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi import FastAPI

from countersign.api.dashboard import DemoBar, build_demo_router
from countersign.api.friday import DemoActivityMiddleware, Friday
from countersign.config import Settings


class DB:
    def __init__(self):
        self.active = False

    @asynccontextmanager
    async def tx(self):
        yield self

    async def execute(self, *_args):
        return self

    async def fetchone(self):
        return {"active":self.active}


def controller(tmp_path):
    runtime = Settings("postgresql://fake:fake@localhost/countersign_c62_demo")
    bar = DemoBar(runtime, tmp_path / "week", "http://127.0.0.1:8198")
    db, clock = DB(), [0]
    result = Friday(db, runtime, bar, tmp_path / "bundle", clock=lambda:clock[0], idle_minutes=1)
    bar.friday = result
    result.fresh = AsyncMock(return_value=False)
    return result, clock


@pytest.mark.asyncio
async def test_idle_uses_activity_clock_and_skips_busy_owner_and_challenge(tmp_path):
    c, clock = controller(tmp_path)
    c.restore = AsyncMock()
    assert not await c.idle_once()
    clock[0] = 61
    c.owner_active = 1
    assert not await c.idle_once()
    c.owner_active = 0
    c.database.active = True
    assert not await c.idle_once()
    c.database.active = False
    c.fresh.return_value = True
    assert not await c.idle_once()
    c.fresh.return_value = False
    c.touch()
    assert not await c.idle_once()
    clock[0] = 122
    assert await c.idle_once()
    assert not await c.idle_once()
    await c.bar.task
    c.restore.assert_awaited_once()
    c.minutes = 0
    clock[0] = 9999
    assert not await c.idle_once()


@pytest.mark.asyncio
async def test_friday_endpoint_busy_unavailable_and_no_network_snapshot_input(tmp_path):
    c, _ = controller(tmp_path)
    app = FastAPI()
    app.include_router(build_demo_router(c.bar), prefix="/api/v1")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        c.bar.friday = None
        assert (await client.post("/api/v1/demo/friday")).json()["error"]["code"] == "DEMO_CHECKPOINT_UNAVAILABLE"
        c.bar.friday = c
        c.owner_active = 1
        assert (await client.post("/api/v1/demo/friday")).json()["error"]["code"] == "DEMO_BUSY"
        c.owner_active = 0
        assert (await client.post("/api/v1/demo/friday", json={"snapshot":{}})).status_code == 422
        assert (await client.post("/api/v1/demo/friday?file=other")).status_code == 422
        c.restore = AsyncMock()
        assert (await client.post("/api/v1/demo/friday")).status_code == 202
        await c.bar.task
        c.broken = True
        assert (await client.post("/api/v1/demo/friday")).json()["error"]["code"] == "DEMO_RESTORE_FAILED"


@pytest.mark.asyncio
async def test_owner_and_restore_share_gate_and_all_posts_touch_clock(tmp_path):
    c, clock = controller(tmp_path)
    app = FastAPI()
    app.add_middleware(DemoActivityMiddleware, state=SimpleNamespace(friday=c))

    @app.post("/api/v1/proposals/fake/approve")
    async def approve():
        assert c.owner_active == 1
        assert c.start() == "DEMO_BUSY"
        return {"ok":True}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        clock[0] = 42
        assert (await client.post("/api/v1/proposals/fake/approve")).status_code == 200
        assert c.last_activity == 42 and c.owner_active == 0
        c.restoring = True
        assert (await client.get("/api/v1/cash")).status_code == 503
        c.restoring = False
        c.bar.task = asyncio.create_task(asyncio.sleep(0.05))
        assert (await client.post("/api/v1/proposals/fake/approve")).status_code == 409
        await c.bar.task


@pytest.mark.asyncio
async def test_failed_restore_is_fixed_error_and_preserves_generation(tmp_path):
    c, _ = controller(tmp_path)
    c.transport = SimpleNamespace(clear_authentication=Mock())
    await c.restore()  # Missing trusted artifact; no SQL or mock writes can start.
    assert c.bar.generation == 0
    assert c.bar.last == {"action":"friday", "ok":False, "code":"DEMO_RESTORE_FAILED"}
    assert not c.restoring


@pytest.mark.asyncio
async def test_database_failure_rolls_back_mock_and_fails_closed(tmp_path, monkeypatch):
    c, _ = controller(tmp_path)
    c.bundle = SimpleNamespace(load=lambda:({}, {}), week=lambda *_args:None)
    c.mock_request = AsyncMock(return_value={})
    monkeypatch.setattr("countersign.api.friday.restore_database", AsyncMock(side_effect=RuntimeError("private failure")))
    await c.restore()
    assert [call.args[0] for call in c.mock_request.await_args_list] == ["validate", "stash", "load", "rollback"]
    assert c.bar.generation == 0 and c.broken
    assert c.start() == "DEMO_RESTORE_FAILED"
    assert "private" not in str(c.bar.last)


@pytest.mark.asyncio
async def test_activity_during_idle_query_prevents_restore(tmp_path):
    c, clock = controller(tmp_path)
    clock[0] = 61
    original = c.database.execute
    async def active_during_query(*args):
        c.touch()
        return await original(*args)
    c.database.execute = active_during_query
    assert not await c.idle_once()
