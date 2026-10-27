"""Expired requests release and replan atomically without granting approval."""

import asyncio
from datetime import datetime, timedelta

import pytest
from test_approvals import NOW, WEEK, planned
from test_approvals import world as shared_world

from countersign.approvals import ApprovalError
from countersign.approvals.service import RUN_REQUEST_LIFETIME
from countersign.db import Repositories

world = shared_world
pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def records(database):
    async with database.tx() as conn:
        repos = Repositories(conn)
        return {name: await getattr(repos, name).find() for name in (
            "payout_runs", "payout_lines", "payout_line_obligations", "proposals",
            "obligations", "reservations", "approvals", "operations", "audit_log",
        )}


async def test_expiry_refresh_is_atomic_idempotent_and_requires_new_hash(world):
    database, service, _, obligation = world
    run, old, binding = await planned(world)
    assert datetime.fromisoformat(binding["expires_at"]) == NOW + RUN_REQUEST_LIFETIME
    assert RUN_REQUEST_LIFETIME == timedelta(hours=24)
    assert service.lifetime == timedelta(minutes=30)
    service.clock = lambda: NOW + RUN_REQUEST_LIFETIME
    # The original refusal still rolls back, and cannot approve any operation.
    with pytest.raises(ApprovalError, match="APPROVAL_EXPIRED"):
        await service.approve(old["id"], binding["payload_hash"])
    assert sorted(await asyncio.gather(service.refresh_run_requests(),
                                      service.refresh_run_requests())) == [0, 1]
    assert await service.refresh_run_requests() == 0
    data = await records(database)
    assert len(data["payout_runs"]) == len(data["proposals"]) == 2
    [fresh] = [p for p in data["proposals"] if p["state"] == "NEEDS_OWNER"]
    assert next(p for p in data["proposals"] if p["id"] == old["id"])["state"] == "EXPIRED"
    assert next(r for r in data["payout_runs"] if r["id"] == run["id"])["state"] == "CANCELLED"
    assert fresh["params"]["run_id"] != str(run["id"])
    new_binding = fresh["verdict_details"]["approval_request"]
    assert new_binding["payload_hash"] != binding["payload_hash"]
    assert datetime.fromisoformat(new_binding["expires_at"]) == service.clock() + RUN_REQUEST_LIFETIME
    assert data["obligations"][0]["state"] == "SCHEDULED"
    assert data["obligations"][0]["version"] == obligation["version"] + 3
    active = [r for r in data["reservations"] if r["state"] == "ACTIVE"]
    assert len(active) == 1 and active[0]["proposal_id"] == fresh["id"]
    assert not data["approvals"] and not data["operations"]
    refreshes = [a for a in data["audit_log"] if a["kind"] == "RUN_REQUEST_REFRESHED"]
    assert [a["details"] for a in refreshes] == [
        {"run_id": str(run["id"]), "week_start": WEEK.isoformat(), "reason": "EXPIRED"}]
    with pytest.raises(ApprovalError, match="STALE_PROPOSAL"):
        await service.approve(fresh["id"], binding["payload_hash"])
    with pytest.raises(ApprovalError, match="STALE_PROPOSAL"):
        await service.approve(old["id"], binding["payload_hash"])
    assert (await service.approve(fresh["id"], new_binding["payload_hash"]))["used_at"] is None


@pytest.mark.parametrize("change", ["version", "payload"])
async def test_stale_binding_refreshes_even_before_expiry(world, change):
    database, service, maker, _ = world
    _, old, _ = await planned(world)
    async with database.tx() as conn:
        if change == "version":
            await Repositories(conn).makers.update(maker["id"], display_name="Updated")
        else:
            # Faulty writer bypasses versioning: canonical hash must still catch it.
            await conn.execute("UPDATE makers SET payout_email='corrected@example.test'")
    assert await service.refresh_run_requests() == 1
    data = await records(database)
    assert next(p for p in data["proposals"] if p["id"] == old["id"])["state"] == "EXPIRED"
    [audit] = [a for a in data["audit_log"] if a["kind"] == "RUN_REQUEST_REFRESHED"]
    assert audit["details"]["reason"] == "STALE"


@pytest.mark.parametrize("refusal", ["NO_PAYABLE_OBLIGATIONS", "SHIELD_BLOCKED"])
async def test_known_replan_refusal_commits_release_without_partial_new_run(world, refusal):
    database, service, _, obligation = world
    run, old, _ = await planned(world)
    service.clock = lambda: NOW + RUN_REQUEST_LIFETIME
    async with database.tx() as conn:
        repos = Repositories(conn)
        if refusal == "NO_PAYABLE_OBLIGATIONS":
            await repos.obligations.update(obligation["id"], state="RESERVED")
        else:
            [balance] = await repos.balance_snapshots.find()
            await repos.balance_snapshots.update(balance["id"], available_minor=1)
    assert await service.refresh_run_requests() == 1
    assert await service.refresh_run_requests() == 0
    data = await records(database)
    assert len(data["payout_runs"]) == len(data["proposals"]) == 1
    assert data["payout_runs"][0]["state"] == "CANCELLED"
    assert data["proposals"][0]["state"] == "EXPIRED"
    assert not any(r["state"] == "ACTIVE" for r in data["reservations"])
    assert not any(r["active"] for r in data["payout_line_obligations"])
    assert not data["operations"]
    [audit] = [a for a in data["audit_log"] if a["kind"] == "RUN_REQUEST_REFRESHED"]
    assert audit["proposal_id"] == old["id"] and audit["details"] == {
        "run_id": str(run["id"]), "week_start": WEEK.isoformat(),
        "reason": "EXPIRED", "code": refusal}


async def test_unexpected_failure_rolls_back_release_and_replan(world, monkeypatch):
    database, service, _, _ = world
    await planned(world)
    service.clock = lambda: NOW + RUN_REQUEST_LIFETIME
    before = await records(database)

    async def crash(conn, week):
        raise RuntimeError("SIMULATED_CRASH")

    monkeypatch.setattr(service, "_plan_run", crash)
    with pytest.raises(RuntimeError, match="SIMULATED_CRASH"):
        await service.refresh_run_requests()
    assert await records(database) == before


@pytest.mark.parametrize("decision", ["reject", "approve"])
async def test_owner_decisions_are_never_auto_refreshed(world, decision):
    database, service, _, _ = world
    run, old, binding = await planned(world)
    if decision == "reject":
        await service.reject(old["id"], "Owner declined")
    else:
        await service.approve(old["id"], binding["payload_hash"])
    service.clock = lambda: NOW + timedelta(days=2)
    before = await records(database)
    assert await service.refresh_run_requests() == 0
    assert await records(database) == before
    if decision == "reject":
        fresh = await service.plan_run(WEEK)
        assert fresh["id"] != run["id"] and fresh["state"] == "AWAITING_OWNER"
    else:
        with pytest.raises(ApprovalError, match="RUN_ALREADY_EXISTS"):
            await service.plan_run(WEEK)


async def test_dashboard_lists_live_week_once_and_preserves_detail_and_grants(world):
    database, service, _, _ = world
    run, _, _ = await planned(world)
    service.clock = lambda: NOW + RUN_REQUEST_LIFETIME
    assert await service.refresh_run_requests() == 1
    async with database.tx() as conn:
        await conn.execute("SET LOCAL ROLE countersign_agent_ro")
        [live] = await (await conn.execute("SELECT doc FROM v_dash_runs")).fetchall()
        assert live["doc"]["id"] != str(run["id"])
        assert live["doc"]["total"] == {"minor": 900, "currency": "USD"}
        [detail] = await (await conn.execute("SELECT doc FROM v_dash_run_detail")).fetchall()
        assert detail["doc"]["id"] == live["doc"]["id"]
        assert len(detail["doc"]["lines"]) == 1
