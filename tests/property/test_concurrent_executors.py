"""Randomized races run two real executor instances against the local mock only."""

import asyncio
import json
from datetime import date
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from countersign.config import PayPalSettings
from countersign.executor import Executor
from countersign.harness import Engine
from countersign.paypal.http import PayPalHTTP

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@settings(
    max_examples=8, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(seed_id=st.integers(0, 65535), parallel=st.integers(2, 4), seconds=st.integers(31, 180))
async def test_two_real_executors_create_one_attempt_for_the_same_line(
    world, seed_id, parallel, seconds
):
    scenario = json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "eval/scenarios/F01_timeout_after_payout_created.json"
        ).read_text()
    )
    # Resetting the mock invalidates OAuth tokens. Each generated example owns
    # a fresh HTTP client as well as fresh DB rows, not a stale cached token.
    async with (
        PayPalHTTP(
            PayPalSettings(base_url=world.settings.paypal_base_url), transport=world.transport
        ) as http,
        Engine(world.settings, database=world.database, http=http) as engine,
    ):
        await engine.reset()
        assert (
            await world.controls.post("/_mock/reset", json={"seed": seed_id})
        ).status_code == 200
        assert (
            await world.controls.post("/_mock/seed", json=scenario["initial_state"]["paypal"])
        ).status_code == 200
        await engine.seed(world.state)
        await engine.plan_payout_run(date(2026, 11, 2))
        [pending] = await engine.pending_approvals()
        await engine.approve(pending["id"], pending["approval_request"]["payload_hash"])
        first = Executor.from_http(world.database, http, clock=world.settings.clock)
        other = Executor.from_http(world.database, http, clock=world.settings.clock)
        executors = (first, other)
        await asyncio.gather(*(executors[i % 2].execute(pending["id"]) for i in range(parallel)))
        async with world.database.tx() as conn:
            cursor = await conn.execute(
                "SELECT line_id,count(*) AS count FROM payout_attempts GROUP BY line_id"
            )
            assert [row["count"] for row in await cursor.fetchall()] == [1]
        await world.controls.post("/_mock/clock/advance", json={"seconds": seconds})
        assert (await engine.tick())["errors"] == 0
        await asyncio.gather(first.execute(pending["id"]), other.execute(pending["id"]))
        truth = (await world.controls.get("/_mock/ground-truth")).json()
        assert len(truth["payout_items"]) == 1
        assert (
            sum(
                r["method"] == "POST" and r["path"] == "/v1/payments/payouts"
                for r in truth["requests"]
            )
            == 1
        )
        async with world.database.tx() as conn:
            cursor = await conn.execute("SELECT state FROM obligations")
            assert [row["state"] for row in await cursor.fetchall()] == ["SETTLED"]
