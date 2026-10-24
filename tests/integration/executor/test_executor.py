"""Executor against isolated PostgreSQL and the in-process PayPal mock; fake data only."""

import asyncio
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
import pytest_asyncio

from countersign.approvals import ApprovalService
from countersign.config import PayPalSettings
from countersign.db import Database, Repositories
from countersign.executor import Executor, ExecutorError
from countersign.paypal.http import PayPalHTTP
from countersign.paypal.read import PayPalRead
from countersign.paypal.write import PayPalWrite
from mock_paypal.app import create_app

NOW = datetime(2026, 11, 2, 9, tzinfo=UTC)
WEEK = date(2026, 11, 2)
BASE = "http://localhost:8099"
pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


class Counting(httpx.AsyncBaseTransport):
    """Counts the payout POSTs the executor actually sends."""

    def __init__(self, inner):
        self.inner, self.payout_posts = inner, 0

    async def handle_async_request(self, request):
        if request.method == "POST" and request.url.path == "/v1/payments/payouts":
            self.payout_posts += 1
        return await self.inner.handle_async_request(request)


class Clock:
    def __init__(self):
        self.now = NOW

    def __call__(self):
        return self.now


@pytest_asyncio.fixture
async def world(test_db_url):
    app = create_app(control_enabled=True)
    transport = httpx.ASGITransport(app=app)
    counting = Counting(transport)
    clock = Clock()
    async with (
        Database(test_db_url, max_size=6) as database,
        httpx.AsyncClient(transport=transport, base_url=BASE) as controls,
        PayPalHTTP(PayPalSettings(base_url=BASE), transport=counting) as http,
    ):
        controls.counting = counting
        await controls.post("/_mock/seed", json={"balances": {"available": "1500.00"}})
        async with database.tx() as conn:
            repos = Repositories(conn)
            for name, cents in (("Maker A", 900), ("Maker B", 4860)):
                maker = await repos.makers.insert(
                    display_name=name,
                    payout_email=f"{name[-1].lower()}@example.test",
                    payout_email_verified_at=NOW - timedelta(days=1),
                )
                await repos.obligations.insert(
                    maker_id=maker["id"],
                    kind="ADJUSTMENT",
                    amount_minor=cents,
                    currency="USD",
                    due_date=WEEK,
                    state="OPEN",
                )
            await repos.balance_snapshots.insert(
                fetched_at=NOW,
                last_refresh_time=NOW,
                currency="USD",
                available_minor=100000,
                withheld_minor=0,
                raw={},
            )
        executor = Executor(database, PayPalWrite(http), PayPalRead(http), clock=clock)
        yield database, ApprovalService(database, clock=clock), executor, controls, clock


async def approved(world):
    database, service, *_ = world
    run = await service.plan_run(WEEK)
    async with database.tx() as conn:
        proposal = await Repositories(conn).proposals.get(run["proposal_id"])
    await service.approve(
        proposal["id"], proposal["raw_output"]["approval_request"]["payload_hash"]
    )
    return run, proposal["id"]


async def rows(database, table, **filters):
    async with database.tx() as conn:
        return await Repositories(conn)[table].find(filters or None, limit=1000)


async def items(controls):
    return (await controls.get("/_mock/ground-truth")).json()["payout_items"]


async def drop_next_payout_response(controls):
    response = await controls.post(
        "/_mock/faults",
        json={
            "match": {"method": "POST", "path": "^/v1/payments/payouts$"},
            "action": {"type": "drop_response", "after_commit": True},
        },
    )
    assert response.status_code == 201


async def test_approved_run_pays_each_line_once_and_consumes_the_approval(world):
    database, _, executor, controls, _ = world
    _, proposal_id = await approved(world)

    status = await executor.execute(proposal_id)

    assert status["proposal_state"] == "EXECUTED"
    assert set(status["operations"].values()) == {"SUCCEEDED"}
    attempts = await rows(database, "payout_attempts")
    assert [a["state"] for a in attempts] == ["SUBMITTED", "SUBMITTED"]
    assert all(a["paypal_batch_id"] for a in attempts)
    assert {a["sender_batch_id"] for a in attempts} == {f"cs-{a['line_id']}-a1" for a in attempts}
    assert len(await items(controls)) == 2
    [approval] = await rows(database, "approvals")
    assert approval["used_at"] == NOW and approval["used_by_operation_id"] is not None
    assert {o["state"] for o in await rows(database, "obligations")} == {"IN_FLIGHT"}
    assert {line["state"] for line in await rows(database, "payout_lines")} == {"PAYING"}
    [stored_run] = await rows(database, "payout_runs")
    assert stored_run["state"] == "DONE"
    assert {r["state"] for r in await rows(database, "reservations")} >= {"CONSUMED"}
    assert not [r for r in await rows(database, "reservations") if r["state"] == "ACTIVE"]
    journals = await rows(database, "ledger_journals")
    assert sorted(j["memo"].split(":")[0] for j in journals) == ["PAYOUT_SUBMITTED"] * 2

    again = await executor.execute(proposal_id)
    assert again == status
    assert len(await items(controls)) == 2


async def test_lost_response_stays_unknown_until_an_identical_replay_confirms_it(world):
    database, _, executor, controls, _ = world
    _, proposal_id = await approved(world)
    await drop_next_payout_response(controls)

    status = await executor.execute(proposal_id)

    assert status["proposal_state"] == "EXECUTING"
    assert sorted(status["operations"].values()) == ["SUCCEEDED", "UNKNOWN"]
    assert len(await items(controls)) == 2  # PayPal did create the dropped one
    unknown = [a for a in await rows(database, "payout_attempts") if a["state"] == "UNKNOWN"]
    assert len(unknown) == 1 and unknown[0]["paypal_batch_id"] is None

    swept = await executor.sweep()

    assert set(swept["resolved"].values()) == {"SUCCEEDED"}
    assert len(await items(controls)) == 2  # the replay created nothing new
    assert {a["state"] for a in await rows(database, "payout_attempts")} == {"SUBMITTED"}
    assert (await executor.status(proposal_id))["proposal_state"] == "EXECUTED"
    journals = await rows(database, "ledger_journals")
    assert len(journals) == 2  # one PAYOUT_SUBMITTED per operation, never twice
    kinds = [a["kind"] for a in await rows(database, "audit_log")]
    assert "OPERATION_UNKNOWN" in kinds and "REPLAY_RESOLVED" in kinds


async def test_two_concurrent_executors_produce_one_attempt_per_line(world):
    database, _, executor, controls, _ = world
    _, proposal_id = await approved(world)

    first, second = await asyncio.gather(
        executor.execute(proposal_id), executor.execute(proposal_id)
    )

    assert len(await rows(database, "payout_attempts")) == 2
    assert len(await rows(database, "operations")) == 2
    assert len(await items(controls)) == 2
    assert "EXECUTED" in {first["proposal_state"], second["proposal_state"]}


@pytest.mark.parametrize("change", ["maker", "expired"])
async def test_stale_or_expired_approval_blocks_without_calling_paypal(world, change):
    database, _, executor, controls, clock = world
    _, proposal_id = await approved(world)
    if change == "maker":
        async with database.tx() as conn:
            repos = Repositories(conn)
            [maker, _] = await repos.makers.find()
            await repos.makers.update(maker["id"], display_name="Changed after approval")
    else:
        clock.now = NOW + timedelta(minutes=31)

    status = await executor.execute(proposal_id)

    assert status["proposal_state"] == "BLOCKED_AT_EXECUTION"
    assert set(status["operations"].values()) == {"BLOCKED"}
    assert await items(controls) == []
    assert controls.counting.payout_posts == 0
    assert await rows(database, "payout_attempts") == []
    [approval] = await rows(database, "approvals")
    assert approval["used_at"] is None
    assert {o["state"] for o in await rows(database, "obligations")} == {"OPEN"}
    [stored_run] = await rows(database, "payout_runs")
    assert stored_run["state"] == "CANCELLED"
    assert not [r for r in await rows(database, "reservations") if r["state"] == "ACTIVE"]
    voided = [a for a in await rows(database, "audit_log") if a["kind"] == "APPROVAL_VOIDED"]
    assert len(voided) == 1


async def test_definite_rejection_leaves_the_line_unpaid_for_a_replacement(world):
    database, _, executor, controls, _ = world
    _, proposal_id = await approved(world)
    await controls.post("/_mock/seed", json={"balances": {"available": "0.10"}})

    status = await executor.execute(proposal_id)

    assert status["proposal_state"] == "FAILED"
    assert set(status["operations"].values()) == {"FAILED"}
    assert {a["state"] for a in await rows(database, "payout_attempts")} == {"REJECTED"}
    assert {line["state"] for line in await rows(database, "payout_lines")} == {"UNPAID"}
    assert {o["state"] for o in await rows(database, "obligations")} == {"SCHEDULED"}
    assert await rows(database, "ledger_journals") == []


async def test_abandoned_flight_becomes_unknown_and_is_replayed_not_resent(world):
    _database, _, executor, controls, clock = world
    _, proposal_id = await approved(world)
    prepared = await executor._prepare(proposal_id)  # the process dies before the call
    assert {o["state"] for o in prepared} == {"IN_FLIGHT"}
    clock.now = NOW + timedelta(minutes=11)

    swept = await executor.sweep()

    assert swept["recovered"] == 2
    assert set(swept["resolved"].values()) == {"SUCCEEDED"}
    assert len(await items(controls)) == 2
    assert (await executor.status(proposal_id))["proposal_state"] == "EXECUTED"


async def test_unknown_past_the_replay_window_goes_to_the_owner_without_a_call(world):
    database, _, executor, controls, clock = world
    _, proposal_id = await approved(world)
    await drop_next_payout_response(controls)
    await executor.execute(proposal_id)
    before = controls.counting.payout_posts
    assert before == 2
    clock.now = NOW + timedelta(days=26)

    swept = await executor.sweep()
    await executor.sweep()

    assert set(swept["resolved"].values()) == {"UNKNOWN"}
    [incident] = await rows(database, "incidents")
    assert incident["state"] == "AWAITING_OWNER"
    assert incident["subject"]["reason"] == "UNKNOWN_PAST_REPLAY_WINDOW"
    assert controls.counting.payout_posts == before
    assert (await executor.status(proposal_id))["proposal_state"] == "EXECUTING"


async def test_non_paying_actions_are_refused_before_any_change(world):
    database, _, executor, _, _ = world
    async with database.tx() as conn:
        proposal = await Repositories(conn).proposals.insert(
            action="PROVIDE_EVIDENCE",
            params={},
            evidence_refs=[],
            model="test",
            raw_output={},
            state="ALLOWED",
        )
    with pytest.raises(ExecutorError, match="UNSUPPORTED_ACTION"):
        await executor.execute(proposal["id"])
    [stored] = await rows(database, "proposals")
    assert stored["state"] == "ALLOWED"
