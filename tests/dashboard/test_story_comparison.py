"""W2 reads the actual staged week; it never recomputes a historical simulation."""

import asyncio
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import psycopg
import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from psycopg import sql
from psycopg.conninfo import make_conninfo
from sqlalchemy import create_engine

from countersign.api.dashboard import DbSource, build_router
from countersign.config import Settings
from scripts import demo_week as demo

ROOT = Path(__file__).resolve().parents[2]


@contextmanager
def empty_schema():
    base = os.environ.get("COUNTERSIGN_TEST_DATABASE_URL")
    if not base:
        pytest.skip("COUNTERSIGN_TEST_DATABASE_URL is unset")
    schema = "cs_w2_" + uuid4().hex
    dsn = make_conninfo(base, options=f"-csearch_path={schema},public")
    with psycopg.connect(base, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    engine = create_engine("postgresql+psycopg://", creator=lambda: psycopg.connect(dsn))
    try:
        with engine.begin() as connection:
            config = Config(str(ROOT / "migrations/alembic.ini"))
            config.attributes.update(connection=connection, version_table_schema=schema)
            command.upgrade(config, "head")
        yield SimpleNamespace(conninfo=dsn)
    finally:
        engine.dispose()
        with psycopg.connect(base, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def run(coro):
    return asyncio.run(coro, loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32"
                       else asyncio.new_event_loop)


@pytest.mark.parametrize("mode,mock", [("live", False), ("live", True), ("demo", False)])
def test_comparison_is_hidden_outside_demo_mock(mode, mock):
    async def check():
        app = FastAPI()
        app.state.settings = SimpleNamespace(mode=mode, paypal=SimpleNamespace(is_mock=mock))
        app.include_router(build_router(), prefix="/api/v1")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get("/api/v1/demo/comparison")).status_code == 404
    run(check())


@pytest.mark.integration
def test_staged_week_comparison_trace_and_moments(tmp_path, monkeypatch):
    # Reuse C25's real mock, Engine, checkpoint and fake scripted model. Do not
    # fork their implementations or synthesize the expected financial evidence.
    with monkeypatch.context() as imports:
        imports.syspath_prepend(str(ROOT / "tests/integration"))
        from test_demo_week_mock import EMAILS, demo_world

    async def check(schema):
        async with demo_world(schema, tmp_path, monkeypatch) as world:
            app = FastAPI()
            app.state.settings = Settings("postgresql://fake@localhost/week_test")
            app.state.database = world.database
            async with DbSource(schema.conninfo, runtime=app.state.settings) as source:
                app.include_router(build_router(source), prefix="/api/v1")
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                    assert (await client.get("/api/v1/demo/comparison")).json() == {
                        "available": False, "needs": "friday_plan"}
                    ordinary_step = world.store.step
                    reports = {}

                    async def observed_step(name, callback):
                        await ordinary_step(name, callback)
                        if name in {"friday_plan", "friday_execute", "safety_blocks", "following_week_pay_a"}:
                            reports[name] = (await client.get("/api/v1/demo/comparison")).json()
                        if name == "friday_plan":
                            simulation = await world.engine._simulator.simulate({
                                "run_id": reports[name]["run_id"], "respect_reserves": True})
                            assert simulation["payout"]["minor"] == 108600
                            assert sum(m["amount"]["minor"] for m in simulation["makers"]) == 108600
                            assert sorted(m["amount"]["minor"] for m in simulation["makers"]) == [48600, 60000]

                    monkeypatch.setattr(world.store, "step", observed_step)
                    await demo.run_week(world.engine, world.store, http=world.http, control=world.control,
                        owner=world.owner, model=world.model, advance_to=world.advance_to,
                        manual=world.manual, emails=EMAILS, options=demo.DemoOptions())
                    friday = reports["friday_plan"]
                    assert friday["naive"]["source"] == "simulated" and friday["naive"]["recorded_at"]
                    assert friday["naive"]["payout"] == {"minor": 150000, "currency": "USD"}
                    assert friday["naive"]["disputed_shares"]["minor"] == 41400
                    assert friday["naive"]["shortfall"]["minor"] == 0
                    assert friday["naive"]["unreserved_after"]["minor"] == 30000
                    assert friday["countersign"]["total"]["minor"] == 108600
                    assert sum(m["amount"]["minor"] for m in friday["naive"]["makers"]) == 150000
                    assert sum(m["disputed_shares"]["minor"] for m in friday["naive"]["makers"]) == 41400
                    assert sorted(m["amount"]["minor"] for m in friday["naive"]["makers"]) == [36000, 54000, 60000]
                    assert sum(m["amount"]["minor"] for m in friday["countersign"]["held_back"]) == 41400
                    assert all(value is None for value in friday["moments"].values())
                    recovered = reports["friday_execute"]["moments"]["lost_response"]
                    assert recovered["payment_count"] == 1 and recovered["exactly_one"]
                    assert recovered["duplicate_400_linked"]
                    assert reports["friday_execute"]["moments"]["injection"] is None
                    final = reports["following_week_pay_a"]
                    assert final["naive"] == friday["naive"]
                    assert final["countersign"]["total"]["minor"] == 108600
                    assert sum(m["amount"]["minor"] for m in final["countersign"]["held_back"]) == 5400
                    assert {"INJECTION_GUARD", "PAYEE_INTEGRITY"} <= set(final["moments"]["injection"]["block_rules"])
                    assert "SETTLEMENT" in final["moments"]["settled_retry"]["block_rules"]
                    proposals = (await client.get("/api/v1/proposals", params={"limit": 200})).json()["items"]
                    planner = next(p for p in proposals if p["model"] == "deterministic-planner")
                    assert planner["agent_trace"] is None
                    evidence = next(p for p in proposals if p["action"] == "PROVIDE_EVIDENCE")
                    assert evidence["rationale"].startswith("Scripted:")
                    assert evidence["agent_trace"]["rounds"] == 2
                    assert [c["tool"] for c in evidence["agent_trace"]["calls"]] == ["get_dispute", "get_shipment_tracking"]
                    single = (await client.get("/api/v1/proposals/" + evidence["id"])).json()
                    assert single["agent_trace"] == evidence["agent_trace"]
                    async with world.database.tx() as conn:
                        # Saving a second comparison would be a write bug. Reads
                        # and completed-step resume must not create another row.
                        n = (await (await conn.execute("SELECT count(*) AS n FROM audit_log "
                            "WHERE details->>'event'='DEMO_NAIVE_COMPARISON'")).fetchone())["n"]
                        assert n == 1
                        await conn.execute("DELETE FROM audit_log WHERE details->>'event'='DEMO_NAIVE_COMPARISON'")
                    assert (await client.get("/api/v1/demo/comparison")).json()["available"] is False

    with empty_schema() as schema:
        run(check(schema))
