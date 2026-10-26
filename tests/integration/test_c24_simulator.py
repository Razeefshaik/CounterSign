import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest
from test_harness import WEEK, ready_poll
from test_harness import world as shared_world

from countersign.agent.reads import CustomReads
from countersign.db import Repositories
from countersign.reconcile import PollResult
from countersign.simulator import RunSimulator, SimulationError

world = shared_world
pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def seeded(world):
    await world.engine.seed(world.state)
    await mirrored_rows(world, "balance_snapshots")
    return world.settings.clock()


async def mirrored_rows(world, table, filters=None):
    # A busy reconciliation cycle can report zero errors without reading PayPal.
    # Wait for the required observation, not merely an error-free tick.
    async with asyncio.timeout(60):
        while True:
            await ready_poll(world.engine)
            async with world.database.tx() as conn:
                rows = await getattr(Repositories(conn), table).find(filters)
            if rows:
                return rows
            await asyncio.sleep(0.02)


async def snapshot_rows(world):
    async with world.database.tx() as conn:
        repos = Repositories(conn)
        return {
            name: await getattr(repos, name).find()
            for name in (
                "incidents",
                "proposals",
                "operations",
                "reservations",
                "obligations",
                "policies",
                "audit_log",
            )
        }


async def add_dispute(world):
    response = await world.controls.post(
        "/_mock/disputes",
        json={
            "capture_id": world.state["orders"][0]["capture_id"],
            "amount": "540.00",
            "reason": "MERCHANDISE_OR_SERVICE_NOT_RECEIVED",
        },
    )
    assert response.status_code == 200
    rows = await mirrored_rows(
        world, "disputes", {"paypal_dispute_id": response.json()["dispute_id"]}
    )
    return rows[0]


@pytest.mark.parametrize("busy_cycles", [0, 2])
async def test_simulator_with_and_without_dispute_reserve_never_writes(
    world, monkeypatch, busy_cycles
):
    poller = world.engine._reconciler.reconciler
    original = poller.poll_once
    remaining = busy_cycles

    async def sometimes_busy():
        nonlocal remaining
        if remaining:
            remaining -= 1
            return PollResult(busy=True)
        return await original()

    monkeypatch.setattr(poller, "poll_once", sometimes_busy)
    now = await seeded(world)
    async with world.database.tx() as conn:
        repos = Repositories(conn)
        order = (await repos.orders.find())[0]
        await repos.obligations.insert(
            maker_id=order["maker_id"],
            order_id=None,
            kind="ORDER_SHARE",
            amount_minor=10000,
            currency="USD",
            due_date=WEEK,
            state="OPEN",
        )
    remaining = busy_cycles
    await add_dispute(world)
    run_id = await world.engine.plan_payout_run(WEEK)
    before = await snapshot_rows(world)
    simulator = RunSimulator(world.database)
    protected = await simulator.simulate({"run_id": run_id})
    exposed = await simulator.simulate({"run_id": run_id, "respect_reserves": False})
    assert await simulator.simulate({"run_id": run_id}) == protected
    assert await simulator.simulate({"run_id": run_id, "respect_reserves": False}) == exposed
    assert protected["payout"] == {"minor": 10000, "currency": "USD"}
    assert exposed["payout"] == {"minor": 58600, "currency": "USD"}
    assert protected["effective_available"] == exposed["effective_available"]
    assert protected["unreserved_after"]["minor"] - exposed["unreserved_after"]["minor"] == 48600
    assert protected["dispute_earmarks_not_subtracted"]["minor"] == 48600
    assert not protected["pays_disputed_shares"] and exposed["pays_disputed_shares"]
    assert not exposed["executable"]
    assert protected["own_cash_reservation_replaced"]["minor"] == 10000
    reads = CustomReads(None, None, simulator)
    result = await reads.simulate({"run_id": run_id})
    assert result["available"] and result["simulation"] == protected
    assert before == await snapshot_rows(world)
    assert now == world.settings.clock()


async def test_simulator_subtracts_outflows_after_refresh_and_other_reserves(world):
    now = await seeded(world)
    run_id = await world.engine.plan_payout_run(WEEK)
    async with world.database.tx() as conn:
        repos = Repositories(conn)
        await repos.reservations.insert(
            kind="PROPOSAL_CASH", amount_minor=100, currency="USD", state="ACTIVE"
        )
        operation = await repos.operations.insert(
            kind="DISPUTE_ACCEPT_CLAIM",
            target_type="dispute",
            target_id=uuid4(),
            payload={},
            payload_hash=bytes(32),
            state="SUCCEEDED",
            submitted_at=now,
        )
        from countersign.ledger import journal
        from countersign.money import Money

        await journal.post_journal(
            conn,
            journal.refund(Money(200, "USD"), source=journal.AVAILABLE),
            operation_id=operation["id"],
            occurred_at=now + timedelta(seconds=1),
        )
    result = await RunSimulator(world.database).simulate({"run_id": run_id})
    assert result["outflows_after_refresh"]["minor"] == 200
    assert result["other_cash_reservations"]["minor"] == 100
    assert (
        result["unreserved_after"]["minor"]
        == result["snapshot_available"]["minor"] - 200 - 100 - 48600
    )


async def test_simulator_database_enforces_read_only(world, monkeypatch):
    await seeded(world)
    run_id = await world.engine.plan_payout_run(WEEK)
    from countersign.simulator import service

    async def forbidden(conn, *args, **kwargs):
        await conn.execute("INSERT INTO policies(key,value) VALUES ('forbidden','true')")

    monkeypatch.setattr(service, "simulate_run", forbidden)
    with pytest.raises(SimulationError, match="SIMULATION_UNAVAILABLE"):
        await RunSimulator(world.database).simulate({"run_id": run_id})
    async with world.database.tx() as conn:
        assert await Repositories(conn).policies.get("forbidden") is None


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"run_id": "bad"},
        {"run_id": 3},
        {"run_id": str(uuid4()), "respect_reserves": 1},
        {"run_id": str(uuid4()), "sql": "DELETE"},
    ],
)
async def test_simulator_rejects_untrusted_arguments(world, arguments):
    with pytest.raises(SimulationError):
        await RunSimulator(world.database).simulate(arguments)


async def test_demo_week_cash_and_dispute_shares_match_exactly(world):
    now = await seeded(world)
    async with world.database.tx() as conn:
        repos = Repositories(conn)
        maker = (await repos.makers.find())[0]
        await repos.obligations.insert(
            maker_id=maker["id"],
            kind="ORDER_SHARE",
            amount_minor=60000,
            currency="USD",
            due_date=WEEK,
            state="OPEN",
        )
        share = await repos.obligations.insert(
            maker_id=maker["id"],
            kind="ORDER_SHARE",
            amount_minor=41400,
            currency="USD",
            due_date=WEEK,
            state="RESERVED",
        )
        await repos.reservations.insert(
            kind="DISPUTE_EARMARK",
            obligation_id=share["id"],
            amount_minor=41400,
            currency="USD",
            state="ACTIVE",
        )
        await repos.balance_snapshots.insert(
            fetched_at=now + timedelta(seconds=1),
            last_refresh_time=now,
            currency="USD",
            available_minor=134000,
            raw={},
        )
    run_id = await world.engine.plan_payout_run(WEEK)
    simulator = RunSimulator(world.database)
    protected = await simulator.simulate({"run_id": run_id})
    exposed = await simulator.simulate({"run_id": run_id, "respect_reserves": False})
    assert protected["payout"]["minor"] == 108600
    assert protected["unreserved_after"]["minor"] == 25400
    assert protected["dispute_earmarks_not_subtracted"]["minor"] == 41400
    assert exposed["payout"]["minor"] == 150000
    assert exposed["shortfall"]["minor"] == 16000
