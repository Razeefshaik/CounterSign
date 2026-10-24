"""Owner decisions against isolated PostgreSQL; fake local-only data."""

import asyncio
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from psycopg import IntegrityError

from countersign.api import owner, runs
from countersign.approvals import (
    ApprovalError,
    ApprovalService,
    canonical_payload_hash,
    load_context,
)
from countersign.config import PayPalSettings, Settings
from countersign.db import Database, Repositories
from countersign.shield.snapshot import Approval

NOW = datetime(2026, 11, 2, 9, tzinfo=UTC)
WEEK = date(2026, 11, 2)
pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest_asyncio.fixture
async def world(test_db_url):
    async with Database(test_db_url, max_size=4) as database:
        async with database.tx() as conn:
            repos = Repositories(conn)
            maker = await repos.makers.insert(
                display_name="Maker A",
                payout_email="maker@example.test",
                payout_email_verified_at=NOW - timedelta(days=1),
            )
            obligation = await repos.obligations.insert(
                maker_id=maker["id"],
                kind="ADJUSTMENT",
                amount_minor=900,
                currency="USD",
                due_date=WEEK,
                state="OPEN",
            )
            await repos.balance_snapshots.insert(
                fetched_at=NOW,
                last_refresh_time=NOW,
                currency="USD",
                available_minor=10000,
                withheld_minor=0,
                raw={},
            )
        yield database, ApprovalService(database, clock=lambda: NOW), maker, obligation


async def planned(world):
    database, service, _, _ = world
    run = await service.plan_run(WEEK)
    async with database.tx() as conn:
        proposal = await Repositories(conn).proposals.get(run["proposal_id"])
    return run, proposal, proposal["raw_output"]["approval_request"]


async def test_plan_and_approve_bind_exact_current_versions_and_stay_unused(world):
    database, service, _, obligation = world
    run, proposal, pending = await planned(world)
    assert run["state"] == "AWAITING_OWNER"
    assert proposal["state"] == "NEEDS_OWNER"
    assert proposal["params"]["lines"][0]["amount"] == {"minor": 900, "currency": "USD"}
    assert proposal["expected_effect"] == {"available_change": {"minor": -900, "currency": "USD"}}
    assert pending["record_versions"][f"obligation:{obligation['id']}"] == 2
    assert (
        canonical_payload_hash(proposal["raw_output"]["operation_payload"])
        == pending["payload_hash"]
    )
    approval = await service.approve(proposal["id"], pending["payload_hash"])
    assert approval["used_at"] is None
    async with database.tx() as conn:
        repos = Repositories(conn)
        stored = await repos.approvals.get(approval["id"])
        updated = await repos.proposals.get(proposal["id"])
        shield_proposal, snap, _ = await load_context(conn, updated, now=NOW)
        assert stored["record_versions"] == snap.record_versions()
        assert bytes(stored["payload_hash"]).hex() == pending["payload_hash"][7:]
        assert stored["used_at"] is None and stored["used_by_operation_id"] is None
        assert (await repos.payout_runs.get(run["id"]))["state"] == "APPROVED"
        reservations = await repos.reservations.find({"state": "ACTIVE"})
        assert [r["kind"] for r in reservations] == ["RUN_CASH"]
        assert snap.cash.unreserved.minor == 9100
        assert not await repos.operations.find()
        # Binding itself is valid at the execution boundary; C19 supplies write-ahead.
        assert (
            Approval(
                str(stored["id"]),
                str(proposal["id"]),
                shield_proposal.payload_hash,
                stored["record_versions"],
                stored["expires_at"],
            ).used_at
            is None
        )


@pytest.mark.parametrize("change", ["obligation", "maker", "hash"])
async def test_changed_versions_or_hash_are_stale_without_approval(world, change):
    database, service, maker, obligation = world
    _, proposal, pending = await planned(world)
    async with database.tx() as conn:
        repos = Repositories(conn)
        if change == "obligation":
            await repos.obligations.update(obligation["id"], amount_minor=901)
        if change == "maker":
            await repos.makers.update(maker["id"], display_name="Updated")
    with pytest.raises(ApprovalError, match="STALE_PROPOSAL"):
        await service.approve(
            proposal["id"], "sha256:" + "0" * 64 if change == "hash" else pending["payload_hash"]
        )
    async with database.tx() as conn:
        assert await Repositories(conn).approvals.find() == []


async def test_expired_request_and_duplicate_approval_fail_closed(world):
    database, service, _, _ = world
    _, proposal, pending = await planned(world)
    service.clock = lambda: NOW + timedelta(hours=1)
    with pytest.raises(ApprovalError, match="APPROVAL_EXPIRED"):
        await service.approve(proposal["id"], pending["payload_hash"])
    service.clock = lambda: NOW
    await service.approve(proposal["id"], pending["payload_hash"])
    with pytest.raises(ApprovalError, match="STALE_PROPOSAL"):
        await service.approve(proposal["id"], pending["payload_hash"])
    async with database.tx() as conn:
        assert len(await Repositories(conn).approvals.find()) == 1


async def test_rejection_releases_commitment_and_allocation(world):
    database, service, _, obligation = world
    run, proposal, _ = await planned(world)
    rejected = await service.reject(proposal["id"], "Defer for private-owner@example.test")
    assert rejected["state"] == "REJECTED"
    async with database.tx() as conn:
        repos = Repositories(conn)
        assert (await repos.obligations.get(obligation["id"]))["state"] == "OPEN"
        assert (await repos.payout_runs.get(run["id"]))["state"] == "CANCELLED"
        assert await repos.reservations.find({"state": "ACTIVE"}) == []
        assert await repos.payout_line_obligations.find({"active": True}) == []
        assert await repos.approvals.find() == []
        audit = (await repos.audit_log.find({"kind": "OWNER_REJECTED"}))[0]
        assert audit["details"] == {"reason_provided": True}


async def test_rejection_still_works_without_cash_observation(world):
    database, service, _, _ = world
    _, proposal, _ = await planned(world)
    async with database.tx() as conn:
        repo = Repositories(conn).balance_snapshots
        for balance in await repo.find():
            await repo.delete(balance["id"])
    assert (await service.reject(proposal["id"], "Missing reporting data"))["state"] == "REJECTED"


async def test_new_cash_commitment_is_rechecked_before_approval(world):
    database, service, _, _ = world
    _, proposal, pending = await planned(world)
    async with database.tx() as conn:
        await Repositories(conn).reservations.insert(
            kind="PROPOSAL_CASH", amount_minor=9900, currency="USD", state="ACTIVE"
        )
    with pytest.raises(ApprovalError, match="SHIELD_BLOCKED"):
        await service.approve(proposal["id"], pending["payload_hash"])
    async with database.tx() as conn:
        assert await Repositories(conn).approvals.find() == []


async def test_canonical_manifest_detects_unversioned_tampering(world):
    database, service, _, _ = world
    _, proposal, pending = await planned(world)
    async with database.tx() as conn:
        # Even a faulty writer bypassing the version API cannot reuse a changed body.
        await conn.execute("UPDATE payout_lines SET amount_minor=901")
    with pytest.raises(ApprovalError, match="STALE_PROPOSAL"):
        await service.approve(proposal["id"], pending["payload_hash"])


async def test_competing_week_planners_allocate_obligation_once(world):
    database, service, _, _ = world
    outcomes = await asyncio.gather(
        service.plan_run(WEEK), service.plan_run(WEEK), return_exceptions=True
    )
    assert sum(isinstance(r, dict) for r in outcomes) == 1
    assert sum(isinstance(r, ApprovalError) for r in outcomes) == 1
    async with database.tx() as conn:
        repos = Repositories(conn)
        assert len(await repos.payout_line_obligations.find({"active": True})) == 1
        assert len(await repos.reservations.find({"state": "ACTIVE"})) == 1


@pytest.mark.parametrize("problem", ["funding", "payee", "cash"])
async def test_blocked_planning_rolls_back_every_money_change(world, problem):
    database, service, maker, obligation = world
    async with database.tx() as conn:
        repos = Repositories(conn)
        if problem == "payee":
            await repos.makers.update(maker["id"], payout_email_verified_at=None)
        elif problem == "funding":
            balance = (await repos.balance_snapshots.find())[0]
            await repos.balance_snapshots.update(balance["id"], available_minor=1)
        else:
            balance = (await repos.balance_snapshots.find())[0]
            await repos.balance_snapshots.delete(balance["id"])
    with pytest.raises(ApprovalError, match="SHIELD_BLOCKED|CASH_UNKNOWN"):
        await service.plan_run(WEEK)
    async with database.tx() as conn:
        repos = Repositories(conn)
        assert await repos.payout_runs.find() == []
        assert await repos.payout_lines.find() == []
        assert await repos.proposals.find() == []
        assert (await repos.obligations.get(obligation["id"]))["state"] == "OPEN"


async def test_competing_approvals_create_one_unused_row(world):
    database, service, _, _ = world
    _, proposal, pending = await planned(world)
    outcomes = await asyncio.gather(
        service.approve(proposal["id"], pending["payload_hash"]),
        service.approve(proposal["id"], pending["payload_hash"]),
        return_exceptions=True,
    )
    assert sum(isinstance(r, dict) for r in outcomes) == 1
    assert sum(isinstance(r, ApprovalError) for r in outcomes) == 1
    async with database.tx() as conn:
        assert len(await Repositories(conn).approvals.find()) == 1


async def test_owner_email_update_invalidates_pending_approval(world):
    _, service, maker, _ = world
    _, proposal, pending = await planned(world)
    changed = await service.set_payout_email(maker["id"], "corrected@example.test")
    assert changed["version"] == 2
    assert changed["payout_email_verified_at"] == NOW
    with pytest.raises(ApprovalError, match="STALE_PROPOSAL"):
        await service.approve(proposal["id"], pending["payload_hash"])


async def test_disputed_and_earmarked_open_shares_are_excluded(world):
    database, _, maker, _ = world
    async with database.tx() as conn:
        repos = Repositories(conn)
        order = await repos.orders.insert(
            maker_id=maker["id"], buyer_ref="buyer", amount_minor=40000, currency="USD"
        )
        await repos.obligations.insert(
            maker_id=maker["id"],
            order_id=order["id"],
            kind="ORDER_SHARE",
            amount_minor=36000,
            currency="USD",
            due_date=WEEK,
            state="OPEN",
        )
        await repos.disputes.insert(
            paypal_dispute_id="PP-D-STAGED", order_id=order["id"], status="OPEN", phase="ACTIVE"
        )
    run, _, _ = await planned(world)
    async with database.tx() as conn:
        lines = await Repositories(conn).payout_lines.find({"run_id": run["id"]})
        assert [r["amount_minor"] for r in lines] == [900]


async def test_owner_routes_auth_and_validation_then_approve(world):
    database, _, maker, _ = world
    settings = Settings(
        "postgresql://fake:fake@localhost/fake",
        mode="live",
        paypal=PayPalSettings(
            base_url="https://api-m.sandbox.paypal.com",
            client_id="fake-id",
            client_secret="fake-secret",
        ),
        owner_token="fake-owner-token",
    )
    app = FastAPI()
    app.include_router(owner.build_router(database, settings), prefix="/api/v1")
    app.include_router(runs.build_router(database, settings), prefix="/api/v1")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://local"
    ) as client:
        response = await client.post("/api/v1/payout-runs", json={"week_start": WEEK.isoformat()})
        assert response.status_code == 401
        client.headers["X-Owner-Token"] = "fake-owner-token"
        bad = await client.post(
            f"/api/v1/makers/{maker['id']}/payout-email",
            json={"payout_email": "changed@example.test", "confirm": False},
        )
        assert bad.status_code == 422
        assert bad.json()["error"]["code"] == "INVALID_REQUEST"
        assert "changed@example.test" not in bad.text
        good = await client.post(
            f"/api/v1/makers/{maker['id']}/payout-email",
            json={"payout_email": "changed@example.test", "confirm": True},
        )
        assert good.status_code == 200
        assert "payout_email" not in good.json()
        assert good.json()["payout_email_verified"] is True
        assert "changed@example.test" not in good.text
        # API uses real clock, so fixtures' verified_at is safely in the past here.
        async with database.tx() as conn:
            await Repositories(conn).makers.update(
                maker["id"], payout_email_verified_at=datetime.now(UTC) - timedelta(days=1)
            )
        response = await client.post("/api/v1/payout-runs", json={"week_start": WEEK.isoformat()})
        assert response.status_code == 201, response.text
        assert response.json()["total"] == {"minor": 900, "currency": "USD"}
        proposal_id = UUID(response.json()["proposal_id"])
        async with database.tx() as conn:
            proposal = await Repositories(conn).proposals.get(proposal_id)
        response = await client.post(
            f"/api/v1/proposals/{proposal_id}/approve",
            json={"payload_hash": proposal["raw_output"]["approval_request"]["payload_hash"]},
        )
        assert response.status_code == 201, response.text
        assert response.json()["approval"]["used_at"] is None


async def test_approval_uses_its_own_cash_reservation_once(world):
    database, service, _, _ = world
    async with database.tx() as conn:
        repo = Repositories(conn).balance_snapshots
        balance = (await repo.find())[0]
        await repo.update(balance["id"], available_minor=900)
    _, proposal, pending = await planned(world)
    assert (await service.approve(proposal["id"], pending["payload_hash"]))["used_at"] is None


async def test_demo_owner_reject_returns_public_proposal_and_stale_http_code(world):
    database, _, _, _ = world
    _, proposal, _ = await planned(world)
    app = FastAPI()
    app.include_router(
        owner.build_router(database, Settings("postgresql://fake:fake@localhost/fake")),
        prefix="/api/v1",
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://local"
    ) as client:
        stale = await client.post(
            f"/api/v1/proposals/{proposal['id']}/approve",
            json={"payload_hash": "sha256:" + "0" * 64},
        )
        assert stale.status_code == 409
        assert stale.json()["error"]["code"] == "STALE_PROPOSAL"
        response = await client.post(
            f"/api/v1/proposals/{proposal['id']}/reject", json={"reason": "Owner defers"}
        )
    assert response.status_code == 200
    public = response.json()
    assert public["state"] == "REJECTED" and public["tier"] == "OWNER"
    assert public["rule_results"]
    assert "raw_output" not in public and "verdict_details" not in public
    assert "maker@example.test" not in response.text


async def test_owner_http_storage_errors_do_not_expose_driver_values(world, monkeypatch):
    database, _, _, _ = world

    async def unavailable(*args, **kwargs):
        raise IntegrityError("fake-private-owner@example.test fake-private-value")

    monkeypatch.setattr(ApprovalService, "plan_run", unavailable)
    app = FastAPI()
    app.include_router(
        runs.build_router(database, Settings("postgresql://fake:fake@localhost/fake")),
        prefix="/api/v1",
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://local"
    ) as client:
        response = await client.post("/api/v1/payout-runs", json={"week_start": WEEK.isoformat()})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "OWNER_STORAGE_UNAVAILABLE"
    assert "fake-private" not in response.text


@pytest.mark.parametrize("changed", [False, True])
async def test_public_dispute_id_binds_the_internal_dispute_version(world, changed):
    database, service, _, _ = world
    async with database.tx() as conn:
        repos = Repositories(conn)
        dispute = await repos.disputes.insert(
            paypal_dispute_id="PP-D-OWNER",
            status="OPEN",
            phase="ACTIVE",
            amount_minor=100,
            currency="USD",
        )
        row = await repos.proposals.insert(
            action="ACCEPT_CLAIM",
            params={"paypal_dispute_id": "PP-D-OWNER"},
            evidence_refs=[],
            model="test",
            raw_output={},
            state="NEEDS_OWNER",
        )
        proposal, snapshot, _ = await load_context(conn, row, now=NOW)
        assert snapshot.record_versions() == {f"dispute:{dispute['id']}": 1}
        pending = {
            "payload_hash": proposal.payload_hash,
            "record_versions": snapshot.record_versions(),
            "expires_at": (NOW + timedelta(minutes=30)).isoformat(),
        }
        await repos.proposals.update(
            row["id"],
            verdict_details={"approval_request": pending},
            raw_output={"approval_request": pending},
        )
        if changed:
            await repos.disputes.update(dispute["id"], status="UNDER_REVIEW")
    if changed:
        with pytest.raises(ApprovalError, match="STALE_PROPOSAL"):
            await service.approve(row["id"], proposal.payload_hash)
    else:
        result = await service.approve(row["id"], proposal.payload_hash)
        async with database.tx() as conn:
            stored = await Repositories(conn).approvals.get(result["id"])
            assert stored["record_versions"] == pending["record_versions"]
            assert stored["used_at"] is None


@pytest.mark.parametrize("params", [{}, {"paypal_dispute_id": "PP-D-MISSING"}, []])
async def test_claim_approval_refuses_missing_or_malformed_targets(world, params):
    database, _, _, _ = world
    async with database.tx() as conn:
        row = await Repositories(conn).proposals.insert(
            action="ACCEPT_CLAIM",
            params=params,
            evidence_refs=[],
            model="test",
            raw_output={},
            state="NEEDS_OWNER",
        )
        with pytest.raises(ApprovalError, match="STALE_PROPOSAL"):
            await load_context(conn, row, now=NOW)
        assert await Repositories(conn).approvals.find() == []
