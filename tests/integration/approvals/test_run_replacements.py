"""Returned historical payouts are part of one exact weekly owner approval."""

from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest
import pytest_asyncio

from countersign.approvals import ApprovalError, ApprovalService, load_context
from countersign.db import Database, Repositories
from countersign.incidents import checks
from countersign.shield.engine import Verdict, evaluate
from countersign.shield.rules.idempotency import derive_keys
from countersign.simulator import RunSimulator
from countersign.simulator.service import run_replacements

NOW = datetime(2026, 11, 6, 9, tzinfo=UTC)
WEEK = date(2026, 11, 2)
pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest_asyncio.fixture
async def replacement_world(test_db_url):
    async with Database(test_db_url) as database:
        async with database.tx() as conn:
            repos = Repositories(conn)
            makers = {}
            for name in ("B", "C"):
                makers[name] = await repos.makers.insert(
                    display_name="Maker " + name,
                    payout_email=name.lower() + "@example.test",
                    payout_email_verified_at=NOW - timedelta(days=1),
                )
            old_run = await repos.payout_runs.insert(
                week_start=WEEK - timedelta(days=7), state="DONE"
            )
            old_line = await repos.payout_lines.insert(
                run_id=old_run["id"],
                maker_id=makers["C"]["id"],
                amount_minor=30000,
                currency="USD",
                state="UNPAID",
            )
            old_share = await repos.obligations.insert(
                maker_id=makers["C"]["id"],
                kind="ADJUSTMENT",
                amount_minor=30000,
                currency="USD",
                due_date=WEEK - timedelta(days=7),
                state="SCHEDULED",
            )
            await repos.payout_line_obligations.insert(
                line_id=old_line["id"],
                obligation_id=old_share["id"],
                amount_minor=30000,
            )
            operation = await repos.operations.insert(
                kind="PAYOUT_CREATE",
                target_type="payout_line",
                target_id=old_line["id"],
                payload={},
                payload_hash=bytes(32),
                state="SUCCEEDED",
            )
            keys = derive_keys(str(old_line["id"]), 1)
            raw = {
                "payout_item_id": "OLD-ITEM",
                "payout_batch_id": "OLD-BATCH",
                "transaction_status": "RETURNED",
                "sender_batch_id": keys.sender_batch_id,
                "payout_item": {
                    "sender_item_id": keys.sender_item_id,
                    "receiver": "old-c@example.test",
                    "amount": {"value": "300.00", "currency": "USD"},
                },
            }
            old_attempt = await repos.payout_attempts.insert(
                line_id=old_line["id"],
                attempt_no=1,
                operation_id=operation["id"],
                sender_batch_id=keys.sender_batch_id,
                request_id=keys.request_id,
                sender_item_id=keys.sender_item_id,
                payee_email="old-c@example.test",
                amount_minor=30000,
                currency="USD",
                state="RETURNED",
                paypal_item_id="OLD-ITEM",
                paypal_batch_id="OLD-BATCH",
                paypal_raw=raw,
                prepared_at=NOW - timedelta(days=7),
                last_checked_at=NOW,
            )
            await repos.balance_snapshots.insert(
                fetched_at=NOW,
                last_refresh_time=NOW,
                currency="USD",
                available_minor=134000,
                withheld_minor=46000,
                raw={},
            )
        yield SimpleNamespace(
            database=database,
            service=ApprovalService(database, clock=lambda: NOW),
            makers=makers,
            old_line=old_line,
            old_share=old_share,
            old_attempt=old_attempt,
        )


async def add_current(world, *, negative=False):
    async with world.database.tx() as conn:
        repos = Repositories(conn)
        for maker, minor in (("B", 48600), ("C", -10000 if negative else 30000)):
            await repos.obligations.insert(
                maker_id=world.makers[maker]["id"],
                kind="ADJUSTMENT",
                amount_minor=minor,
                currency="USD",
                due_date=WEEK,
                state="OPEN",
            )


async def proposal_for(world, run):
    async with world.database.tx() as conn:
        return await Repositories(conn).proposals.get(run["proposal_id"])


async def test_expired_run_refresh_keeps_historical_replacement_identity(replacement_world):
    world = replacement_world
    await add_current(world)
    old_run = await world.service.plan_run(WEEK)
    original = await proposal_for(world, old_run)
    world.service.clock = lambda: NOW + timedelta(hours=24)
    assert await world.service.refresh_run_requests() == 1
    async with world.database.tx() as conn:
        repos = Repositories(conn)
        [pending] = await repos.proposals.find({"state": "NEEDS_OWNER"})
        assert pending["params"]["replacements"] == original["params"]["replacements"]
        assert pending["expected_effect"]["available_change"]["minor"] == -108600
        assert (await repos.payout_lines.get(world.old_line["id"]))["state"] == "UNPAID"
        assert (await repos.obligations.get(world.old_share["id"]))["state"] == "SCHEDULED"
        assert len(await repos.payout_attempts.find({"line_id": world.old_line["id"]})) == 1
        assert len(await repos.payout_line_obligations.find(
            {"line_id": world.old_line["id"], "active": True})) == 1


@pytest.mark.parametrize("with_current", [False, True])
async def test_plan_binds_returned_line_once_and_simulates_its_cash(
    replacement_world, with_current
):
    world = replacement_world
    if with_current:
        await add_current(world)
    run = await world.service.plan_run(WEEK)
    proposal = await proposal_for(world, run)
    [entry] = proposal["params"]["replacements"]
    assert entry == {
        "line_id": str(world.old_line["id"]),
        "maker_id": str(world.makers["C"]["id"]),
        "attempt_no": 2,
        "amount": {"minor": 30000, "currency": "USD"},
    }
    total = 108600 if with_current else 30000
    assert proposal["expected_effect"]["available_change"]["minor"] == -total
    request = proposal["verdict_details"]["approval_request"]
    assert f"line:{world.old_line['id']}" in request["record_versions"]
    assert f"obligation:{world.old_share['id']}" in request["record_versions"]
    simulation = await RunSimulator(world.database).simulate({"run_id": str(run["id"])})
    assert simulation["payout"]["minor"] == total
    assert simulation["unreserved_after"]["minor"] == 134000 - total
    await world.service.approve(proposal["id"], request["payload_hash"])
    async with world.database.tx() as conn:
        repos = Repositories(conn)
        approved = await repos.proposals.get(proposal["id"])
        p, snap, payload = await load_context(conn, approved, now=NOW)
        assert evaluate(p, snap).verdict is not Verdict.BLOCK
        item = next(item for item in snap.payouts if item.line_id == str(world.old_line["id"]))
        assert item.attempt_no == 2 and item.payee_email == "c@example.test"
        expected = derive_keys(str(world.old_line["id"]), 2)
        assert (item.request_id, item.sender_batch_id) == (
            expected.request_id,
            expected.sender_batch_id,
        )
        assert (
            sum(
                int(body["body"]["items"][0]["amount"]["value"].replace(".", ""))
                for body in payload["payouts"]
            )
            == total
        )
        assert (await repos.payout_lines.get(world.old_line["id"]))["state"] == "UNPAID"
        assert len(await repos.obligations.find()) == (3 if with_current else 1)
        assert len(await repos.payout_attempts.find()) == 1


async def test_same_returned_line_cannot_be_claimed_by_two_live_runs(replacement_world):
    world = replacement_world
    await world.service.plan_run(WEEK)
    with pytest.raises(ApprovalError, match="NO_PAYABLE_OBLIGATIONS"):
        await world.service.plan_run(WEEK + timedelta(days=7))


@pytest.mark.parametrize(
    "field,value",
    [
        ("attempt_no", 3),
        ("maker_id", "00000000-0000-0000-0000-000000000001"),
        ("amount", {"minor": 30001, "currency": "USD"}),
    ],
)
async def test_replacement_manifest_cannot_change_amount_maker_or_attempt(
    replacement_world, field, value
):
    world = replacement_world
    run = await world.service.plan_run(WEEK)
    proposal = await proposal_for(world, run)
    tampered = deepcopy(proposal)
    tampered["params"]["replacements"][0][field] = value
    async with world.database.tx() as conn:
        with pytest.raises(ApprovalError, match="STALE_PROPOSAL"):
            await load_context(conn, tampered, now=NOW)


@pytest.mark.parametrize("change", ["no_get", "wrong_identity", "not_returned"])
async def test_replacement_requires_identity_matched_returned_get(replacement_world, change):
    world = replacement_world
    async with world.database.tx() as conn:
        repo = Repositories(conn).payout_attempts
        if change == "no_get":
            await repo.update(world.old_attempt["id"], last_checked_at=None)
        else:
            raw = deepcopy(world.old_attempt["paypal_raw"])
            if change == "wrong_identity":
                raw["payout_item"]["receiver"] = "different@example.test"
            else:
                raw["transaction_status"] = "UNCLAIMED"
            await repo.update(world.old_attempt["id"], paypal_raw=raw)
    with pytest.raises(ApprovalError, match="NO_PAYABLE_OBLIGATIONS"):
        await world.service.plan_run(WEEK)


async def mark_old_attempt(world, state, raw_status=None):
    async with world.database.tx() as conn:
        raw = deepcopy(world.old_attempt["paypal_raw"])
        raw["transaction_status"] = raw_status or state
        await Repositories(conn).payout_attempts.update(
            world.old_attempt["id"], state=state, paypal_raw=raw
        )


async def test_get_confirmed_failed_line_is_resent_once_after_approval(replacement_world):
    world = replacement_world
    await mark_old_attempt(world, "FAILED")
    run = await world.service.plan_run(WEEK)
    proposal = await proposal_for(world, run)
    [entry] = proposal["params"]["replacements"]
    assert (entry["line_id"], entry["attempt_no"]) == (str(world.old_line["id"]), 2)
    request = proposal["verdict_details"]["approval_request"]
    await world.service.approve(proposal["id"], request["payload_hash"])
    async with world.database.tx() as conn:
        approved = await Repositories(conn).proposals.get(proposal["id"])
        p, snap, _ = await load_context(conn, approved, now=NOW)
        assert evaluate(p, snap).verdict is not Verdict.BLOCK
    with pytest.raises(ApprovalError, match="NO_PAYABLE_OBLIGATIONS"):
        await world.service.plan_run(WEEK + timedelta(days=7))


@pytest.mark.parametrize(
    "state,raw_status",
    [("ONHOLD", None), ("BLOCKED", None), ("FAILED", "ONHOLD"), ("FAILED", "RETURNED")],
)
async def test_held_or_mismatched_not_paid_status_never_plans_resend(
    replacement_world, state, raw_status
):
    world = replacement_world
    await mark_old_attempt(world, state, raw_status)
    with pytest.raises(ApprovalError, match="NO_PAYABLE_OBLIGATIONS"):
        await world.service.plan_run(WEEK)


async def test_approved_failed_resend_goes_stale_if_status_changes(replacement_world):
    world = replacement_world
    await mark_old_attempt(world, "FAILED")
    run = await world.service.plan_run(WEEK)
    proposal = await proposal_for(world, run)
    await mark_old_attempt(world, "ONHOLD")
    async with world.database.tx() as conn:
        with pytest.raises(ApprovalError, match="STALE_PROPOSAL"):
            await load_context(conn, proposal, now=NOW)


async def test_negative_adjustment_defers_historical_resend(replacement_world):
    world = replacement_world
    await add_current(world, negative=True)
    run = await world.service.plan_run(WEEK)
    proposal = await proposal_for(world, run)
    assert proposal["params"]["replacements"] == []
    assert proposal["expected_effect"]["available_change"]["minor"] == -48600
    async with world.database.tx() as conn:
        assert (await Repositories(conn).obligations.get(world.old_share["id"]))[
            "state"
        ] == "SCHEDULED"


async def test_rejection_preserves_historical_line_and_allocation(replacement_world):
    world = replacement_world
    await add_current(world)
    run = await world.service.plan_run(WEEK)
    await world.service.reject(run["proposal_id"], "Defer this week")
    async with world.database.tx() as conn:
        repos = Repositories(conn)
        assert (await repos.payout_lines.get(world.old_line["id"]))["state"] == "UNPAID"
        assert (await repos.obligations.get(world.old_share["id"]))["state"] == "SCHEDULED"
        assert (
            await repos.payout_line_obligations.get((world.old_line["id"], world.old_share["id"]))
        )["active"]
    again = await world.service.plan_run(WEEK + timedelta(days=7))
    assert (await proposal_for(world, again))["params"]["replacements"]


async def test_approved_manifest_survives_later_proposal_and_run_checks_old_line(replacement_world):
    world = replacement_world
    run = await world.service.plan_run(WEEK)
    proposal = await proposal_for(world, run)
    await world.service.approve(
        proposal["id"], proposal["verdict_details"]["approval_request"]["payload_hash"]
    )
    async with world.database.tx() as conn:
        repos = Repositories(conn)
        await repos.proposals.insert(
            action="SCHEDULE_PAYOUT_RUN",
            params={"run_id": str(run["id"]), "replacements": []},
            model="untrusted-later-proposal",
            state="BLOCKED",
            raw_output={},
            evidence_refs=[],
        )
        current = await repos.payout_runs.get(run["id"])
        assert await run_replacements(conn, current) == [world.old_line["id"]]
        await repos.payout_runs.update(run["id"], state="EXECUTING")
        await repos.payout_runs.update(run["id"], state="DONE")
    async with world.database.tx() as conn:
        agrees, reason = await checks.run_agrees(conn, str(run["id"]), since=NOW)
        assert not agrees and reason == "RUN_REPLACEMENT_UNCONFIRMED"
        repos = Repositories(conn)
        operation = await repos.operations.insert(
            kind="PAYOUT_CREATE",
            target_type="payout_line",
            target_id=world.old_line["id"],
            payload={},
            payload_hash=bytes(32),
            state="SUCCEEDED",
        )
        keys = derive_keys(str(world.old_line["id"]), 2)
        await repos.payout_attempts.insert(
            line_id=world.old_line["id"],
            attempt_no=2,
            operation_id=operation["id"],
            sender_batch_id=keys.sender_batch_id,
            request_id=keys.request_id,
            sender_item_id=keys.sender_item_id,
            payee_email="c@example.test",
            amount_minor=30000,
            currency="USD",
            state="SUCCESS",
            prepared_at=NOW,
        )
        agrees, reason = await checks.run_agrees(conn, str(run["id"]), since=NOW)
        assert not agrees and reason == "PAYOUT_GET_UNCONFIRMED"
