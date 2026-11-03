"""C62: hosted Friday restore and inactivity coordination, mock/demo only."""

import asyncio
import json
import os
import time
from contextlib import suppress

import httpx

from countersign.api.friday_data import Bundle, FridayError, restore_database


class Friday:
    def __init__(self, database, runtime, bar, directory, *, transport=None,
                 idle_minutes=20, clock=time.monotonic, mock_request=None):
        self.database, self.runtime, self.bar = database, runtime, bar
        self.bundle = Bundle(directory)
        self.transport, self.minutes, self.clock = transport, idle_minutes, clock
        self.last_activity = clock()
        self.owner_active = 0
        self.restoring = self.broken = False
        self.dirty = False
        self.idle_task = None
        self.mock_request = mock_request or self._mock

    async def _mock(self, action):
        async with httpx.AsyncClient(base_url=self.runtime.paypal.base_url, timeout=10,
                                     trust_env=False, follow_redirects=False) as client:
            response = await client.post("/_mock/checkpoints/friday/" + action)
            if response.status_code != 200:
                raise FridayError("DEMO_RESTORE_FAILED")
            return response.json()

    def touch(self):
        self.last_activity = self.clock()

    async def fresh(self):
        state = self.bar.state()
        if self.dirty or state["step"] != 6 or self.bar.running or not (self.bar.root / ".friday").is_file():
            return False
        manifest, _ = self.bundle.load()
        async with self.database.tx() as conn:
            row = await (await conn.execute(
                "SELECT p.state AS proposal_state,r.state AS run_state,"
                "EXISTS(SELECT 1 FROM approvals a WHERE a.proposal_id=p.id) AS approved "
                "FROM proposals p JOIN payout_runs r ON r.id=(p.params->>'run_id')::uuid "
                "WHERE p.id=%s", (manifest["proposal_id"],),
            )).fetchone()
        return bool(row and row["proposal_state"] == "NEEDS_OWNER"
                    and row["run_state"] == "AWAITING_OWNER" and not row["approved"])

    async def state(self):
        state = self.bar.state()
        state["friday_available"] = not self.broken
        try:
            state["checkpoint"] = "friday" if not self.restoring and not self.broken and await self.fresh() else None
        except Exception:  # noqa: BLE001 - a checkpoint read never exposes driver/file text.
            state["checkpoint"] = None
            state["last_result"] = {"action":"friday", "ok":False, "code":"DEMO_RESTORE_FAILED"}
        return state

    def start(self):
        if self.broken:
            return "DEMO_RESTORE_FAILED"
        if self.bar.running or self.owner_active:
            return "DEMO_BUSY"
        self.touch()
        self.bar.action = "friday"
        self.bar.task = asyncio.create_task(self.restore())
        return None

    async def restore(self):
        self.restoring = True
        stashed = False
        loaded = False
        try:
            _manifest, database = self.bundle.load()
            await self.mock_request("validate")
            generation = self.bar.generation + 1
            destination = self.bar.base.with_name(f"{self.bar.base.name}.reset-{generation}")
            while destination.exists():
                generation += 1
                destination = self.bar.base.with_name(f"{self.bar.base.name}.reset-{generation}")
            self.bundle.week(destination, self.runtime, self.bar.app_url)
            await self.mock_request("stash")
            stashed = True
            async with self.database.tx() as conn:
                await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                                   ("countersign:owner-decisions",))
                await self.mock_request("load")
                loaded = True
                await restore_database(conn, database)
            self.bar.generation = generation
            self.dirty = False
            self.bar.last = {"action":"friday", "ok":True, "code":"DEMO_FRIDAY_RESTORED"}
        except Exception:  # noqa: BLE001 - no private driver/file/mock data in errors.
            # A lost COMMIT response cannot prove PostgreSQL rolled back. Even a
            # successful mock rollback must not reopen a possibly mixed state.
            if loaded:
                self.broken = True
            if stashed:
                try:
                    await self.mock_request("rollback")
                except Exception:  # noqa: BLE001 - fail closed across an incomplete rollback.
                    self.broken = True
            self.bar.last = {"action":"friday", "ok":False, "code":"DEMO_RESTORE_FAILED"}
        finally:
            if self.transport is not None:
                self.transport.clear_authentication()
            self.restoring = False
            self.touch()

    async def idle_once(self):
        if not self.minutes or self.bar.running or self.owner_active or self.broken:
            return False
        if self.clock() - self.last_activity < self.minutes * 60 or await self.fresh():
            return False
        async with self.database.tx() as conn:
            row = await (await conn.execute(
                "SELECT EXISTS(SELECT 1 FROM passkey_challenges "
                "WHERE used_at IS NULL AND expires_at>now()) AS active"
            )).fetchone()
        if row["active"] or self.clock() - self.last_activity < self.minutes * 60:
            return False
        return self.start() is None

    async def run_idle(self):
        while True:
            await asyncio.sleep(5)
            with suppress(Exception):
                await self.idle_once()

    async def close(self):
        if self.idle_task is not None:
            self.idle_task.cancel()
            with suppress(asyncio.CancelledError):
                await self.idle_task


def configure(database, runtime, bar, *, transport=None):
    directory = os.environ.get("COUNTERSIGN_FRIDAY_ROOT")
    if not directory or os.environ.get("COUNTERSIGN_FRIDAY_BUILD") == "1":
        return None
    minutes = int(os.environ.get("COUNTERSIGN_HOSTED_IDLE_MINUTES", "20"))
    if not 0 <= minutes <= 10080:
        raise FridayError("HOSTED_IDLE_MINUTES_INVALID")
    controller = Friday(database, runtime, bar, directory, transport=transport, idle_minutes=minutes)
    controller.bundle.load()  # Reject incomplete image artifacts at startup.
    if minutes:
        controller.idle_task = asyncio.create_task(controller.run_idle())
    return controller


class DemoActivityMiddleware:
    """Serialize owner actions against demo commands; static/normal GETs stay open."""
    def __init__(self, app, state):
        self.app, self.state = app, state

    async def __call__(self, scope, receive, send):
        controller = getattr(self.state, "friday", None)
        if scope["type"] != "http" or controller is None:
            return await self.app(scope, receive, send)
        path = scope["path"]
        post = scope["method"] == "POST" and path.startswith("/api/v1/")
        demo = post and path.startswith("/api/v1/demo/")
        owner = post and (path.startswith(("/api/v1/owner/", "/api/v1/proposals/", "/api/v1/makers/")) or path == "/api/v1/payout-runs")
        if demo or owner:
            controller.touch()
        unavailable = controller.broken or (controller.restoring and path.startswith(("/api/v1/", "/mcp")) and path != "/api/v1/demo/state")
        if unavailable or (owner and controller.bar.running):
            code = "DEMO_RESTORE_FAILED" if controller.broken else "DEMO_BUSY"
            body = json.dumps({"error":{"code":code, "message":code.replace("_", " ").lower()}}).encode()
            await send({"type":"http.response.start", "status":503 if unavailable else 409,
                        "headers":[(b"content-type", b"application/json")]})
            return await send({"type":"http.response.body", "body":body})
        if owner:
            controller.owner_active += 1
        async def tracked_send(message):
            if owner and path.startswith("/api/v1/makers/") and message["type"] == "http.response.start" and 200 <= message["status"] < 300:
                controller.dirty = True  # A verified-payee edit invalidates Friday's binding.
            await send(message)
        try:
            return await self.app(scope, receive, tracked_send)
        finally:
            if owner:
                controller.owner_active -= 1
                controller.touch()
