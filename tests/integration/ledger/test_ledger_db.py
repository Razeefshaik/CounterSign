"""Ledger journals and reservations against real PostgreSQL 16 (skipped without the URL).

Each test runs in its own throwaway schema from tests/integration/conftest.py, dropped
afterwards.
"""

import asyncio
from datetime import UTC, date, datetime

import psycopg
import pytest
import pytest_asyncio
from psycopg.rows import dict_row

from countersign.db import Repositories, uuid7
from countersign.ledger import journal
from countersign.ledger.cash import BalanceView, Reservation, cash_position
from countersign.ledger.journal import (
    AVAILABLE,
    WITHHELD,
    JournalConflict,
    LedgerTransactionRequired,
)
from countersign.ledger.reservations import (
    ReservationError,
    ReservationNotActive,
    consume_reservation,
    create_reservation,
    earmark_dispute_share,
    load_active,
    release_reservation,
)
from countersign.money import Money

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

REFRESH = datetime(2026, 11, 5, 6, 0, tzinfo=UTC)
NOW = datetime(2026, 11, 5, 9, 0, tzinfo=UTC)


def usd(dollars: int) -> Money:
    return Money(dollars * 100, "USD")


async def connect(url):
    return await psycopg.AsyncConnection.connect(url, autocommit=True, row_factory=dict_row)


@pytest_asyncio.fixture
async def conn(test_db_url):
    async with await connect(test_db_url) as connection:
        yield connection


async def count(conn, table: str) -> int:
    cursor = await conn.execute(f"SELECT count(*) AS n FROM {table}")  # fixed test tables only
    return (await cursor.fetchone())["n"]


async def make_operation(conn) -> dict:
    return await Repositories(conn).operations.insert(
        kind="PAYOUT_CREATE",
        target_type="payout_attempt",
        target_id=uuid7(),
        payload={"test": True},
        payload_hash=b"\x00" * 32,
        state="PENDING",
    )


async def make_disputed_obligation(conn, name: str, order_dollars: int, share_dollars: int):
    repos = Repositories(conn)
    maker = await repos.makers.insert(display_name=name)
    order = await repos.orders.insert(
        maker_id=maker["id"],
        buyer_ref=f"buyer-{name}",
        amount_minor=order_dollars * 100,
        currency="USD",
    )
    obligation = await repos.obligations.insert(
        maker_id=maker["id"],
        order_id=order["id"],
        kind="ORDER_SHARE",
        amount_minor=share_dollars * 100,
        currency="USD",
        due_date=date(2026, 11, 6),
        state="RESERVED",
    )
    dispute = await repos.disputes.insert(
        paypal_dispute_id=f"PP-D-{uuid7().hex[-12:]}",
        order_id=order["id"],
        status="OPEN",
        amount_minor=order_dollars * 100,
        currency="USD",
        phase="ACTIVE",
    )
    return maker, order, obligation, dispute


async def test_every_builder_journal_commits_and_balances(conn):
    operation = await make_operation(conn)
    maker = await Repositories(conn).makers.insert(display_name="Maker C")
    amount = usd(300)
    drafts = [
        journal.payout_submitted(amount),
        journal.payout_unclaimed(amount),
        journal.payout_returned(amount, was_unclaimed=True),
        journal.payout_failed(amount),
        journal.payout_success(amount, maker["id"]),
        journal.refund(usd(60), source=WITHHELD),
    ]
    async with conn.transaction():
        for draft in drafts:
            await journal.post_journal(conn, draft, operation_id=operation["id"], occurred_at=NOW)
    cursor = await conn.execute(
        "SELECT journal_id, currency, sum(amount_minor) AS total FROM ledger_postings "
        "GROUP BY journal_id, currency"
    )
    rows = await cursor.fetchall()
    assert len(rows) == len(drafts)
    assert all(row["total"] == 0 for row in rows)
    cursor = await conn.execute("SELECT memo FROM ledger_journals ORDER BY id")
    memos = [row["memo"] for row in await cursor.fetchall()]
    assert [memo.split(":")[0] for memo in memos] == [d.event for d in drafts]


async def test_database_rejects_a_hand_made_unbalanced_journal(conn):
    repos = Repositories(conn)
    operation = await make_operation(conn)
    with pytest.raises(psycopg.errors.CheckViolation, match="balance"):
        async with conn.transaction():
            row = await repos.ledger_journals.insert(
                operation_id=operation["id"], occurred_at=NOW, memo="REFUND: hand made"
            )
            await repos.ledger_postings.insert(
                journal_id=row["id"], account="buyer:refunds", amount_minor=6000, currency="USD"
            )
            await repos.ledger_postings.insert(
                journal_id=row["id"], account=AVAILABLE, amount_minor=-5900, currency="USD"
            )
    assert await count(conn, "ledger_journals") == 0
    assert await count(conn, "ledger_postings") == 0


async def test_repeat_post_returns_the_existing_journal(conn):
    operation = await make_operation(conn)
    draft = journal.payout_submitted(usd(486))
    async with conn.transaction():
        first = await journal.post_journal(
            conn, draft, operation_id=operation["id"], occurred_at=NOW
        )
    async with conn.transaction():
        again = await journal.post_journal(
            conn,
            journal.payout_submitted(usd(486)),
            operation_id=operation["id"],
            occurred_at=NOW,
        )
    assert again["id"] == first["id"]
    assert await count(conn, "ledger_journals") == 1
    assert await count(conn, "ledger_postings") == 2
    # A different event on the same operation is its own journal.
    async with conn.transaction():
        other = await journal.post_journal(
            conn, journal.payout_failed(usd(486)), operation_id=operation["id"], occurred_at=NOW
        )
    assert other["id"] != first["id"]


async def test_repeat_with_different_postings_conflicts(conn):
    operation = await make_operation(conn)
    async with conn.transaction():
        await journal.post_journal(
            conn,
            journal.payout_submitted(usd(486)),
            operation_id=operation["id"],
            occurred_at=NOW,
        )
    with pytest.raises(JournalConflict):
        async with conn.transaction():
            await journal.post_journal(
                conn,
                journal.payout_submitted(usd(487)),
                operation_id=operation["id"],
                occurred_at=NOW,
            )
    assert await count(conn, "ledger_postings") == 2


async def test_concurrent_writers_post_once(conn, test_db_url):
    operation = await make_operation(conn)
    draft = journal.payout_submitted(usd(300))
    async with await connect(test_db_url) as second:
        async with conn.transaction():
            first = await journal.post_journal(
                conn, draft, operation_id=operation["id"], occurred_at=NOW
            )

            async def racer():
                async with second.transaction():
                    return await journal.post_journal(
                        second, draft, operation_id=operation["id"], occurred_at=NOW
                    )

            task = asyncio.create_task(racer())
            await asyncio.sleep(0.3)
            assert not task.done()  # blocked on the advisory lock until we commit
        raced = await asyncio.wait_for(task, timeout=10)
    assert raced["id"] == first["id"]
    assert await count(conn, "ledger_journals") == 1


async def test_writer_refuses_autocommit_and_repeatable_read(conn):
    operation = await make_operation(conn)
    draft = journal.payout_submitted(usd(1))
    with pytest.raises(LedgerTransactionRequired):
        await journal.post_journal(conn, draft, operation_id=operation["id"], occurred_at=NOW)
    with pytest.raises(LedgerTransactionRequired, match="READ COMMITTED"):
        async with conn.transaction():
            await conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            await journal.post_journal(conn, draft, operation_id=operation["id"], occurred_at=NOW)
    assert await count(conn, "ledger_journals") == 0
    with pytest.raises(ValueError):
        async with conn.transaction():
            await journal.post_journal(conn, draft, operation_id=None, occurred_at=NOW)


async def test_reservation_moves_are_guarded(conn):
    run = await Repositories(conn).payout_runs.insert(
        week_start=date(2026, 11, 2), state="APPROVED"
    )
    async with conn.transaction():
        held = await create_reservation(conn, "RUN_CASH", usd(1086), run_id=run["id"])
        spent = await create_reservation(conn, "RUN_CASH", usd(10), run_id=run["id"])
    assert held["state"] == "ACTIVE" and held["affects_cash"] is True
    released = await release_reservation(conn, held["id"])
    assert released["state"] == "RELEASED" and released["closed_at"] is not None
    with pytest.raises(ReservationNotActive):
        await consume_reservation(conn, held["id"])
    with pytest.raises(ReservationNotActive):
        await release_reservation(conn, held["id"])
    assert (await consume_reservation(conn, spent["id"]))["state"] == "CONSUMED"
    with pytest.raises(ReservationNotActive):
        await release_reservation(conn, uuid7())
    assert await load_active(conn, "USD") == []


async def test_reservation_needs_its_link(conn):
    with pytest.raises(ReservationError, match="run_id"):
        await create_reservation(conn, "RUN_CASH", usd(1))
    with pytest.raises(ReservationError, match="proposal_id"):
        await create_reservation(conn, "PROPOSAL_CASH", usd(1))
    with pytest.raises(ReservationError, match="kind"):
        await create_reservation(conn, "HOLD", usd(1), run_id=uuid7())
    with pytest.raises(ReservationError, match="positive"):
        await create_reservation(conn, "PROPOSAL_CASH", usd(0), proposal_id=uuid7())
    assert await count(conn, "reservations") == 0


async def test_one_active_earmark_per_dispute_and_obligation(conn):
    _, _, obligation, dispute = await make_disputed_obligation(conn, "Maker A", 400, 360)
    _, _, stranger, _ = await make_disputed_obligation(conn, "Maker X", 50, 45)
    async with conn.transaction():
        first = await earmark_dispute_share(
            conn,
            dispute_id=dispute["id"],
            obligation_id=obligation["id"],
            order_amount=usd(400),
            maker_share_pct=90,
        )
        again = await earmark_dispute_share(
            conn,
            dispute_id=dispute["id"],
            obligation_id=obligation["id"],
            order_amount=usd(400),
            maker_share_pct=90,
        )
    assert again["id"] == first["id"]
    assert first["amount_minor"] == 36000 and first["affects_cash"] is False
    with pytest.raises(ReservationError, match="different amount"):
        async with conn.transaction():
            await earmark_dispute_share(
                conn,
                dispute_id=dispute["id"],
                obligation_id=obligation["id"],
                order_amount=usd(400),
                maker_share_pct=80,
            )
    with pytest.raises(ReservationError, match="not on the disputed order"):
        async with conn.transaction():
            await create_reservation(
                conn,
                "DISPUTE_EARMARK",
                usd(45),
                dispute_id=dispute["id"],
                obligation_id=stranger["id"],
            )
    with pytest.raises(ReservationError, match="obligation"):
        async with conn.transaction():
            await create_reservation(conn, "DISPUTE_EARMARK", usd(1), dispute_id=dispute["id"])
    # Once released, the dispute can be earmarked again (e.g. a re-opened case).
    await release_reservation(conn, first["id"])
    async with conn.transaction():
        renewed = await earmark_dispute_share(
            conn,
            dispute_id=dispute["id"],
            obligation_id=obligation["id"],
            order_amount=usd(400),
            maker_share_pct=90,
        )
    assert renewed["id"] != first["id"]
    assert await count(conn, "reservations") == 2


async def test_thursday_position_reproduces_from_the_database(conn):
    """HANDOFF section 10, Thursday: $1,340 available, $414 earmarked, $1,086 run, $254 free."""
    repos = Repositories(conn)
    async with conn.transaction():
        await repos.balance_snapshots.insert(
            fetched_at=NOW,
            as_of_time=REFRESH,
            last_refresh_time=REFRESH,
            currency="USD",
            available_minor=134000,
            withheld_minor=46000,
            total_minor=180000,
            raw={"source": "test"},
        )
        _, _, obligation_a, dispute_a = await make_disputed_obligation(conn, "Maker A", 400, 360)
        _, _, obligation_b, dispute_b = await make_disputed_obligation(conn, "Maker B", 60, 54)
        await earmark_dispute_share(
            conn,
            dispute_id=dispute_a["id"],
            obligation_id=obligation_a["id"],
            order_amount=usd(400),
            maker_share_pct=90,
        )
        await earmark_dispute_share(
            conn,
            dispute_id=dispute_b["id"],
            obligation_id=obligation_b["id"],
            order_amount=usd(60),
            maker_share_pct=90,
        )
        run = await repos.payout_runs.insert(week_start=date(2026, 11, 2), state="APPROVED")
        await create_reservation(conn, "RUN_CASH", usd(486) + usd(300) + usd(300), run_id=run["id"])

    cursor = await conn.execute(
        "SELECT available_minor, currency, last_refresh_time FROM balance_snapshots "
        "WHERE currency = 'USD' ORDER BY fetched_at DESC LIMIT 1"
    )
    snapshot = await cursor.fetchone()
    balance = BalanceView(
        available=Money(snapshot["available_minor"], snapshot["currency"].strip()),
        last_refresh_time=snapshot["last_refresh_time"],
    )
    reservations = await load_active(conn, "USD")
    assert sorted(reservations, key=lambda r: r.amount.minor) == [
        Reservation("DISPUTE_EARMARK", usd(54)),
        Reservation("DISPUTE_EARMARK", usd(360)),
        Reservation("RUN_CASH", usd(1086)),
    ]
    position = cash_position(balance, outflows=[], reservations=reservations)
    assert position.available == usd(1340)
    assert position.cash_reserved == usd(1086)
    assert position.earmarked == usd(414)
    assert position.unreserved == usd(254)
