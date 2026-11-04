"""C62: real replay artifact, PostgreSQL constraints, and 45-day owner decisions.

Run only this file against an owned database; the trusted artifact is generated
by hosted_start --build-friday, never supplied over a network.
"""
import importlib.util
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI

from countersign.api.dashboard import DEMO_STEPS, DemoBar
from countersign.api.friday_clock import project
from countersign.api.friday_data import Bundle, capture_database, restore_database
from countersign.api.owner import build_router
from countersign.config import Settings
from countersign.db import Database

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def bundle():
    directory = Path(os.environ.get("COUNTERSIGN_FRIDAY_TEST_BUNDLE", "/home/app/friday"))
    if not (directory / "manifest.json").exists():
        pytest.skip("C62 built Friday artifact is unavailable")
    return Bundle(directory)


def normalized(document):
    return {name:sorted(json.dumps(row, sort_keys=True) for row in rows)
            for name, rows in document["tables"].items()}


async def test_restore_equals_real_step_six_and_rolls_back_atomically(test_db_url, tmp_path):
    checkpoint = bundle()
    manifest, original = checkpoint.load()
    runtime = Settings("postgresql://fake:fake@localhost/countersign_c62_demo")
    async with Database(test_db_url) as db:
        async with db.tx() as conn:
            await restore_database(conn, original)
        async with db.tx() as conn:
            assert normalized(await capture_database(conn)) == normalized(original)
            before_cash = (await (await conn.execute("SELECT doc FROM v_dash_cash")).fetchone())["doc"]
            before_makers = await (await conn.execute("SELECT doc FROM v_dash_makers ORDER BY id")).fetchall()
        with pytest.raises(RuntimeError, match="abort"):
            async with db.tx() as conn:
                await restore_database(conn, original)
                raise RuntimeError("abort")
        async with db.tx() as conn:
            assert normalized(await capture_database(conn)) == normalized(original)
            assert (await (await conn.execute("SELECT doc FROM v_dash_cash")).fetchone())["doc"] == before_cash
            assert await (await conn.execute("SELECT doc FROM v_dash_makers ORDER BY id")).fetchall() == before_makers
        destination = checkpoint.week(tmp_path / "week", runtime, "http://127.0.0.1:8198")
        state = DemoBar(runtime, destination, "http://127.0.0.1:8198").state()
        assert state["step"] == 6 and not state["complete"]
        saved = json.loads((destination / manifest["date"] / "state.json").read_text())
        assert all(saved["steps"][name] == "SUCCEEDED" for name, _, _ in DEMO_STEPS[:6])


async def test_cash_and_maker_windows_use_staged_time(test_db_url):
    checkpoint = bundle()
    manifest, original = checkpoint.load()
    staged = datetime.fromisoformat(json.loads((checkpoint.directory / "week" / manifest["date"] / "state.json").read_text())["data"]["mock_now"])
    async with Database(test_db_url) as db:
        async with db.tx() as conn:
            await restore_database(conn, original)
            cash = (await (await conn.execute("SELECT doc FROM v_dash_cash")).fetchone())["doc"]
            makers = [row["doc"] for row in await (await conn.execute("SELECT doc FROM v_dash_makers")).fetchall()]
            amounts = {str(row["maker_id"]):row["amount"] for row in await (await conn.execute(
                "SELECT maker_id,sum(amount_minor)::bigint amount FROM obligations "
                "WHERE state='SETTLED' AND currency='USD' AND updated_at>=%s GROUP BY maker_id",
                (staged - timedelta(days=30),),
            )).fetchall()}
        source = SimpleNamespace(runtime=Settings("postgresql://fake:fake@localhost/countersign_c62_demo"), pool=db.pool, clock=lambda:staged)
        await project(source, "cash", [cash])
        await project(source, "makers", makers)
        assert cash["paypal"]["age_seconds"] == max(0, int((staged - datetime.fromisoformat(cash["paypal"]["last_refresh_time"])).total_seconds()))
        threshold = next((row["value"] for row in original["tables"]["policies"] if row["key"] == "cash_stale_seconds"),10800)
        assert cash["paypal"]["stale"] == (cash["paypal"]["age_seconds"] > threshold)
        assert {doc["id"]:doc["settled_last_30d"]["minor"] for doc in makers} == {doc["id"]:amounts.get(doc["id"],0) for doc in makers}


@pytest.mark.parametrize("method", ["plain", "passkey"])
async def test_restored_friday_approves_45_days_later(test_db_url, monkeypatch, method):
    checkpoint = bundle()
    manifest, original = checkpoint.load()
    staged = datetime.fromisoformat(json.loads((checkpoint.directory / "week" / manifest["date"] / "state.json").read_text())["data"]["mock_now"])
    future = datetime.now(UTC) + timedelta(days=45)
    class Future(datetime):
        @classmethod
        def now(cls, tz=None):
            return future.astimezone(tz) if tz else future.replace(tzinfo=None)

    # Real challenge clock is 45 days later; owner approval still uses staged now.
    monkeypatch.setattr("countersign.approvals.passkeys.datetime", Future)
    monkeypatch.setattr("countersign.approvals.service.datetime", Future)
    async def staged_now(_settings):
        return staged
    monkeypatch.setattr("countersign.api.playground._staged_now", staged_now)
    settings = Settings("postgresql://fake:fake@localhost/countersign_c62_demo",
                        passkey_rp_id="localhost", passkey_origin="http://localhost:8194")
    waiting = next(row for row in original["tables"]["proposals"] if row["id"] == manifest["proposal_id"])
    payload_hash = waiting["verdict_details"]["approval_request"]["payload_hash"]
    async with Database(test_db_url) as db:
        async with db.tx() as conn:
            await restore_database(conn, original)
        app = FastAPI()
        app.include_router(build_router(db, settings), prefix="/api/v1")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost:8194") as client:
            if method == "plain":
                response = await client.post(f"/api/v1/proposals/{manifest['proposal_id']}/approve", json={"payload_hash":payload_hash})
            else:
                # Reuse only the software authenticator helper, not its test suite.
                path = Path(__file__).parent / "approvals/test_passkeys.py"
                spec = importlib.util.spec_from_file_location("c62_authenticator", path)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                auth = module.Authenticator()
                options = (await client.post("/api/v1/owner/passkeys/register/options")).json()
                response = await client.post("/api/v1/owner/passkeys/register/verify", json={
                    "challenge_id":options["challenge_id"], "credential":auth.response(options["publicKey"]["challenge"], register=True)})
                assert response.status_code == 201
                options = (await client.post(f"/api/v1/owner/proposals/{manifest['proposal_id']}/passkey/options", json={"payload_hash":payload_hash})).json()
                response = await client.post(f"/api/v1/owner/proposals/{manifest['proposal_id']}/passkey/approve", json={
                    "payload_hash":payload_hash, "challenge_id":options["challenge_id"], "credential":auth.response(options["publicKey"]["challenge"])})
                async with db.tx() as conn:
                    challenge = await (await conn.execute("SELECT expires_at FROM passkey_challenges WHERE id=%s", (UUID(options["challenge_id"]),))).fetchone()
                    assert future < challenge["expires_at"] <= future + timedelta(minutes=2)
            assert response.status_code == 201, response.json().get("error", {}).get("code")
            async with db.tx() as conn:
                approval = await (await conn.execute("SELECT * FROM approvals WHERE proposal_id=%s", (manifest["proposal_id"],))).fetchone()
                assert approval["used_at"] is None
                await restore_database(conn, original)
                assert not await (await conn.execute("SELECT 1 FROM approvals")).fetchall()
                assert not await (await conn.execute("SELECT 1 FROM passkey_challenges")).fetchall()
                if method == "passkey":
                    assert (await (await conn.execute("SELECT sign_count FROM owner_passkeys")).fetchone())["sign_count"] == 1
