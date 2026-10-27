"""Engine ticks refresh from GET-confirmed facts before dispatching execution."""

from copy import deepcopy
from datetime import timedelta

import pytest
from test_harness import WEEK, initial_state, ready_poll
from test_harness import world as shared_world

from countersign.approvals.service import RUN_REQUEST_LIFETIME
from countersign.db import Repositories
from countersign.harness import EngineError

world = shared_world
pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def seed_two(world):
    state, paypal = initial_state()
    state = deepcopy(state)
    state["makers"].append({"key": "A", "display_name": "Maker A",
                            "payout_email": "a@example.test", "verified": True})
    state["orders"].append({"key": "a", "maker": "A", "amount": {"minor": 40000, "currency": "USD"},
                            "capture_id": "CAP-A", "captured_at": "2026-10-28T12:00:00Z",
                            "tracking": None})
    paypal["registered_receivers"].append("a@example.test")
    paypal["captures"].append({"id": "CAP-A", "order_id": "ORDER-A", "amount": "400.00",
                                "create_time": "2026-10-28T12:00:00Z"})
    assert (await world.controls.post("/_mock/seed", json=paypal)).status_code == 200
    await world.engine.seed(state)
    await ready_poll(world.engine)


async def test_dispute_get_refreshes_run_and_new_approval_pays_b_once(world):
    await seed_two(world)
    old_run = await world.engine.plan_payout_run(WEEK)
    [old] = await world.engine.pending_approvals()
    assert old["expected_effect"]["available_change"]["minor"] == -84600
    assert (await world.controls.post("/_mock/disputes", json={
        "capture_id": "CAP-A", "amount": "400.00", "reason": "MERCHANDISE_OR_SERVICE_NOT_RECEIVED"
    })).status_code == 200
    assert (await world.engine.tick())["errors"] == 0
    [fresh] = await world.engine.pending_approvals()
    assert fresh["id"] != old["id"]
    assert fresh["expected_effect"]["available_change"]["minor"] == -48600
    with pytest.raises(EngineError, match="STALE_PROPOSAL"):
        await world.engine.approve(fresh["id"], old["approval_request"]["payload_hash"])
    await world.engine.approve(fresh["id"], fresh["approval_request"]["payload_hash"])
    for _ in range(3):
        assert (await world.engine.tick())["errors"] == 0
        await world.controls.post("/_mock/clock/advance", json={"seconds": 60})
    truth = (await world.controls.get("/_mock/ground-truth")).json()
    [paid] = truth["payout_items"]
    assert paid["status"] == "SUCCESS" and paid["receiver"] == "maker-b@example.com"
    assert paid["amount"]["value"] == "486.00"
    snapshot = await world.engine.snapshot()
    assert [r["id"] for r in snapshot["runs"]] == [fresh["params"]["run_id"]]
    async with world.database.tx() as conn:
        repos = Repositories(conn)
        [retired] = await repos.payout_runs.find({"state": "CANCELLED"})
        assert str(retired["id"]) == old_run
        [audit] = await repos.audit_log.find({"kind": "RUN_REQUEST_REFRESHED"})
        assert audit["details"]["reason"] == "STALE"
        assert len(await repos.proposals.find({"action": "SCHEDULE_PAYOUT_RUN"})) == 2
        [reserve] = await repos.reservations.find({"kind": "DISPUTE_EARMARK", "state": "ACTIVE"})
        assert reserve["amount_minor"] == 36000
        assert sorted(o["state"] for o in await repos.obligations.find()) == ["RESERVED", "SETTLED"]


async def test_two_expired_ticks_create_one_request_and_rejection_stays_cancelled(world, monkeypatch):
    # The fixture lends Engine an HTTP client: its caller owns the virtual
    # token clock, just as Engine does for clients it constructs itself.
    monkeypatch.setattr(world.http, "_monotonic", lambda: world.settings.clock().timestamp())
    await world.engine.seed(world.state)
    await ready_poll(world.engine)
    await world.engine.plan_payout_run(WEEK)
    [old] = await world.engine.pending_approvals()
    await world.controls.post("/_mock/clock/advance", json={"seconds": int(RUN_REQUEST_LIFETIME.total_seconds())})
    assert (await world.engine.tick())["errors"] == 0
    assert (await world.engine.tick())["errors"] == 0
    [fresh] = await world.engine.pending_approvals()
    assert fresh["id"] != old["id"]
    await world.engine.reject(fresh["id"], "Owner declined")
    await world.controls.post("/_mock/clock/advance", json={"seconds": int(timedelta(days=2).total_seconds())})
    assert (await world.engine.tick())["errors"] == 0
    assert not await world.engine.pending_approvals()
    async with world.database.tx() as conn:
        repos = Repositories(conn)
        assert len(await repos.proposals.find()) == 2
        assert len(await repos.audit_log.find({"kind": "RUN_REQUEST_REFRESHED"})) == 1
    await world.engine.plan_payout_run(WEEK)
    assert len(await world.engine.pending_approvals()) == 1
