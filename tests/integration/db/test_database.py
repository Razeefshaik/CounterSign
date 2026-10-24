"""Real PostgreSQL 16 tests, skipped unless the explicit integration URL is set."""

from datetime import UTC, date, datetime, timedelta
from enum import StrEnum

import psycopg
import pytest
import pytest_asyncio
from psycopg.rows import dict_row

from countersign.db import Database, Repositories, StaleRow, cas_transition
from countersign.db.repositories import TABLES
from countersign.fsm import Machine

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


class State(StrEnum):
    OPEN = "OPEN"
    RESERVED = "RESERVED"


class Event(StrEnum):
    DISPUTE_OPENED = "DISPUTE_OPENED"


MACHINE = Machine(
    "obligation-test", State, Event, {(State.OPEN, Event.DISPUTE_OPENED): State.RESERVED}
)


@pytest_asyncio.fixture
async def db_conn(test_db_url):
    async with await psycopg.AsyncConnection.connect(
        test_db_url, autocommit=True, row_factory=dict_row
    ) as conn:
        yield conn


async def test_pool_tx_commits_and_rolls_back(test_db_url):
    async with Database(test_db_url, max_size=2) as database:
        with pytest.raises(RuntimeError, match="rollback"):
            async with database.tx() as conn:
                await Repositories(conn).makers.insert(display_name="rolled back")
                raise RuntimeError("rollback")
        async with database.tx() as conn:
            assert await Repositories(conn).makers.find() == []
            maker = await Repositories(conn).makers.insert(display_name="committed")
        async with database.tx() as conn:
            assert (await Repositories(conn).makers.get(maker["id"]))["display_name"] == "committed"
    assert database.pool.closed


async def test_stale_cas_updates_nothing(db_conn):
    repos = Repositories(db_conn)
    maker = await repos.makers.insert(display_name="Maker A")
    row = await repos.obligations.insert(
        maker_id=maker["id"],
        kind="ADJUSTMENT",
        amount_minor=1234,
        currency="USD",
        due_date=date(2026, 11, 6),
        state="OPEN",
    )
    async with db_conn.transaction():
        assert (
            await cas_transition(
                db_conn, MACHINE, "obligations", row["id"], "OPEN", 1, "DISPUTE_OPENED"
            )
            == "RESERVED"
        )
    with pytest.raises(StaleRow):
        await cas_transition(
            db_conn, MACHINE, "obligations", row["id"], "OPEN", 1, "DISPUTE_OPENED"
        )
    actual = await repos.obligations.get(row["id"])
    assert (actual["state"], actual["version"]) == ("RESERVED", 2)


async def test_repository_version_updates_are_atomic(db_conn):
    repos = Repositories(db_conn)
    maker = await repos.makers.insert(display_name="Maker A")
    updated = await repos.makers.update(maker["id"], expected_version=1, display_name="Renamed")
    assert updated["version"] == 2
    with pytest.raises(StaleRow):
        await repos.makers.update(maker["id"], expected_version=1, display_name="Stale")
    assert (await repos.makers.get(maker["id"], for_update=True))["display_name"] == "Renamed"
    assert await repos.makers.delete(maker["id"])
    assert not await repos.makers.delete(maker["id"])


async def test_repositories_round_trip_every_slice_table(db_conn):
    repos = Repositories(db_conn)
    now = datetime(2026, 11, 6, tzinfo=UTC)
    created = {}

    async def add(table, **values):
        row = await repos[table].insert(**values)
        created[table] = row
        if TABLES[table].uuid_id:
            assert row["id"].version == 7
        return row

    async with db_conn.transaction():
        maker = await add("makers", display_name="Maker A")
        order = await add(
            "orders",
            maker_id=maker["id"],
            buyer_ref="#1042",
            amount_minor=40000,
            currency="USD",
            tracking={"carrier": "USPS", "number": "TEST"},
        )
        obligation = await add(
            "obligations",
            maker_id=maker["id"],
            order_id=order["id"],
            kind="ORDER_SHARE",
            amount_minor=36000,
            currency="USD",
            due_date=now.date(),
            state="OPEN",
        )
        run = await add("payout_runs", week_start=date(2026, 11, 2), state="DRAFT")
        line = await add(
            "payout_lines",
            run_id=run["id"],
            maker_id=maker["id"],
            amount_minor=36000,
            currency="USD",
            state="APPROVED",
        )
        await add(
            "payout_line_obligations",
            line_id=line["id"],
            obligation_id=obligation["id"],
            amount_minor=36000,
        )
        operation = await add(
            "operations",
            kind="PAYOUT_CREATE",
            target_type="payout_line",
            target_id=line["id"],
            payload={"amount_minor": 36000},
            payload_hash=b"fake-hash",
            state="PENDING",
        )
        await add(
            "payout_attempts",
            line_id=line["id"],
            attempt_no=1,
            operation_id=operation["id"],
            sender_batch_id="test-batch",
            request_id="test-request",
            sender_item_id="test-item",
            payee_email="fake@invalid.test",
            amount_minor=36000,
            currency="USD",
            state="PREPARED",
            paypal_raw={"fake": True},
        )
        dispute = await add(
            "disputes",
            paypal_dispute_id="PP-FAKE",
            order_id=order["id"],
            status="OPEN",
            phase="ACTIVE",
            raw={"staged": True},
            links=[],
        )
        await add(
            "reservations",
            kind="DISPUTE_EARMARK",
            obligation_id=obligation["id"],
            dispute_id=dispute["id"],
            amount_minor=36000,
            currency="USD",
            state="ACTIVE",
        )
        journal = await add(
            "ledger_journals", operation_id=operation["id"], occurred_at=now, memo="balanced test"
        )
        await add(
            "ledger_postings",
            journal_id=journal["id"],
            account="paypal:available",
            amount_minor=-36000,
            currency="USD",
        )
        await repos.ledger_postings.insert(
            journal_id=journal["id"],
            account="maker_payable:test",
            amount_minor=36000,
            currency="USD",
        )
        incident = await add(
            "incidents", kind="DISPUTE", subject={"dispute_id": str(dispute["id"])}, state="OPEN"
        )
        proposal = await add(
            "proposals",
            incident_id=incident["id"],
            action="WAIT",
            params={},
            evidence_refs=[],
            model="fake-model",
            raw_output={},
            state="CREATED",
            verdict_rules=["FUNDING"],
        )
        await add(
            "approvals",
            proposal_id=proposal["id"],
            payload_hash=b"fake-hash",
            record_versions={"maker": 1},
            approved_by="test",
            approved_at=now,
            expires_at=now + timedelta(hours=1),
        )
        await add(
            "webhook_inbox",
            event_id="FAKE-EVENT",
            event_type="FAKE.TYPE",
            headers={},
            raw_body=b'{"fake":true}',
            payload={"fake": True},
            signature="UNVERIFIED",
            status="RECEIVED",
        )
        await add(
            "balance_snapshots",
            fetched_at=now,
            currency="USD",
            available_minor=10000,
            withheld_minor=0,
            total_minor=10000,
            raw={},
        )
        await add("policies", key="fake-policy", value={"enabled": True})
        await add("audit_log", actor="system", kind="STATE_TRANSITION", details={"fake": True})

    assert set(created) == set(TABLES)
    columns = await db_conn.execute(
        "SELECT table_name, column_name FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND table_name = ANY(%s)",
        [list(TABLES)],
    )
    actual_columns = {name: set() for name in TABLES}
    for row in await columns.fetchall():
        actual_columns[row["table_name"]].add(row["column_name"])
    for table, spec in TABLES.items():
        assert actual_columns[table] == spec.columns
        assert await repos[table].find()
        if spec.key:
            key = tuple(created[table][column] for column in spec.key)
            assert await repos[table].get(key) == created[table]
    assert created["orders"]["tracking"]["carrier"] == "USPS"
    assert not created["reservations"]["affects_cash"]
    assert type(created["audit_log"]["id"]) is int
    assert (await repos.policies.update("fake-policy", value={"enabled": False}))["version"] == 2
    key = (line["id"], obligation["id"])
    assert not (await repos.payout_line_obligations.update(key, active=False))["active"]
    assert await repos.webhook_inbox.delete("FAKE-EVENT")
