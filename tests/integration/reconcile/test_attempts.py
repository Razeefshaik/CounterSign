"""Poller-only payout recovery, with a real isolated PostgreSQL 16 schema."""

from copy import deepcopy
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

from countersign.db import Database, Repositories
from countersign.ledger import journal
from countersign.money import Money
from countersign.reconcile.attempts import reconcile_attempts

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
NOW = datetime(2026, 11, 6, tzinfo=UTC)


async def seed(conn, state="SUBMITTED", *, item_id=None):
    repos = Repositories(conn)
    maker = await repos.makers.insert(display_name="Maker", payout_email="maker@example.test")
    obligation = await repos.obligations.insert(
        maker_id=maker["id"],
        kind="ORDER_SHARE",
        amount_minor=1000,
        currency="USD",
        due_date=date(2026, 11, 6),
        state="IN_FLIGHT",
    )
    run = await repos.payout_runs.insert(week_start=date(2026, 11, 2), state="EXECUTING")
    line = await repos.payout_lines.insert(
        run_id=run["id"],
        maker_id=maker["id"],
        amount_minor=1000,
        currency="USD",
        state="PAYING",
    )
    await repos.payout_line_obligations.insert(
        line_id=line["id"],
        obligation_id=obligation["id"],
        amount_minor=1000,
    )
    operation = await repos.operations.insert(
        kind="PAYOUT_CREATE",
        target_type="payout_line",
        target_id=line["id"],
        payload={},
        payload_hash=b"x" * 32,
        state="SUCCEEDED",
    )
    attempt = await repos.payout_attempts.insert(
        line_id=line["id"],
        attempt_no=1,
        operation_id=operation["id"],
        sender_batch_id="sender-batch",
        request_id="request",
        sender_item_id="sender-item",
        payee_email="maker@example.test",
        amount_minor=1000,
        currency="USD",
        state=state,
        paypal_batch_id="BATCH-1",
        paypal_item_id=item_id,
    )
    await journal.post_journal(
        conn,
        journal.payout_submitted(Money(1000, "USD")),
        operation_id=operation["id"],
        occurred_at=NOW,
    )
    return attempt, line, obligation, maker


class Reader:
    def __init__(self, status="PENDING"):
        self.status = status
        self.calls = []
        self.detail = {
            "payout_item_id": "ITEM-1",
            "payout_batch_id": "BATCH-1",
            "payout_item": {
                "sender_item_id": "sender-item",
                "receiver": "maker@example.test",
                "amount": {"value": "10.00", "currency": "USD"},
            },
        }
        self.batch_override = None

    def doc(self):
        return {**deepcopy(self.detail), "transaction_status": self.status}

    async def get_payout_batch(self, resource_id):
        self.calls.append(("batch", resource_id))
        return SimpleNamespace(
            status_code=200,
            body=self.batch_override
            or {
                "batch_header": {
                    "payout_batch_id": "BATCH-1",
                    "sender_batch_header": {"sender_batch_id": "sender-batch"},
                },
                "items": [self.doc()],
            },
        )

    async def get_payout_item(self, resource_id):
        self.calls.append(("item", resource_id))
        return SimpleNamespace(status_code=200, body=self.doc())


async def poll(db, reader):
    async with db.tx() as conn:
        return await reconcile_attempts(conn, reader, now=NOW)


async def states(db, attempt, line, obligation):
    async with db.tx() as conn:
        repos = Repositories(conn)
        return (
            (await repos.payout_attempts.get(attempt["id"]))["state"],
            (await repos.payout_lines.get(line["id"]))["state"],
            (await repos.obligations.get(obligation["id"]))["state"],
        )


async def totals(db):
    async with db.tx() as conn:
        cursor = await conn.execute(
            "SELECT account,sum(amount_minor)::bigint AS minor FROM ledger_postings GROUP BY account"
        )
        return {row["account"]: row["minor"] for row in await cursor.fetchall()}


async def test_dropped_webhooks_batch_discovery_and_success_settles_once(test_db_url):
    async with Database(test_db_url) as db:
        async with db.tx() as conn:
            attempt, line, obligation, maker = await seed(conn)
        reader = Reader()
        assert (await poll(db, reader)).updated == 1
        assert reader.calls == [("batch", "BATCH-1"), ("item", "ITEM-1")]
        reader.status = "SUCCESS"
        assert (await poll(db, reader)).updated == 1
        await poll(db, reader)
        assert await states(db, attempt, line, obligation) == ("SUCCESS", "PAID", "SETTLED")
        values = await totals(db)
        assert values[journal.AVAILABLE] == -1000
        assert values[journal.IN_TRANSIT] == 0
        assert values[journal.maker_payable(maker["id"])] == 1000
        async with db.tx() as conn:
            assert len(await Repositories(conn).ledger_journals.find()) == 2


@pytest.mark.parametrize("final", ["RETURNED", "FAILED"])
async def test_unclaimed_keeps_in_flight_and_return_restores_cash(test_db_url, final):
    async with Database(test_db_url) as db:
        async with db.tx() as conn:
            attempt, line, obligation, _ = await seed(conn, item_id="ITEM-1")
        reader = Reader("UNCLAIMED")
        await poll(db, reader)
        assert await states(db, attempt, line, obligation) == ("UNCLAIMED", "PAYING", "IN_FLIGHT")
        reader.status = final
        await poll(db, reader)
        await poll(db, reader)
        assert await states(db, attempt, line, obligation) == (final, "UNPAID", "SCHEDULED")
        assert all(value == 0 for value in (await totals(db)).values())


async def test_unclaimed_then_success_credits_correct_source(test_db_url):
    async with Database(test_db_url) as db:
        async with db.tx() as conn:
            attempt, line, obligation, maker = await seed(conn, item_id="ITEM-1")
        reader = Reader("UNCLAIMED")
        await poll(db, reader)
        reader.status = "SUCCESS"
        await poll(db, reader)
        assert await states(db, attempt, line, obligation) == ("SUCCESS", "PAID", "SETTLED")
        amounts = await totals(db)
        assert amounts[journal.UNCLAIMED] == amounts[journal.IN_TRANSIT] == 0
        assert amounts[journal.maker_payable(maker["id"])] == 1000


async def test_success_regression_incident_and_no_repeated_settlement(test_db_url):
    async with Database(test_db_url) as db:
        async with db.tx() as conn:
            attempt, line, obligation, _ = await seed(conn, item_id="ITEM-1")
        reader = Reader("SUCCESS")
        await poll(db, reader)
        initial = await totals(db)
        reader.status = "PENDING"
        result = await poll(db, reader)
        assert result.incidents == 1
        assert await states(db, attempt, line, obligation) == ("PENDING", "PAID", "SETTLED")
        reader.status = "SUCCESS"
        await poll(db, reader)
        assert await totals(db) == initial


@pytest.mark.parametrize("status", ["REFUNDED", "REVERSED"])
async def test_post_success_clawback_reopens_without_inventing_line_edge(test_db_url, status):
    async with Database(test_db_url) as db:
        async with db.tx() as conn:
            attempt, line, obligation, _ = await seed(conn, item_id="ITEM-1")
        reader = Reader("SUCCESS")
        await poll(db, reader)
        reader.status = status
        result = await poll(db, reader)
        assert result.incidents >= 1
        assert (await poll(db, reader)).incidents == 0
        assert await states(db, attempt, line, obligation) == (status, "PAID", "OPEN")
        assert all(value == 0 for value in (await totals(db)).values())
        async with db.tx() as conn:
            assert not (await Repositories(conn).payout_line_obligations.find())[0]["active"]


@pytest.mark.parametrize("state", ["PREPARED", "UNKNOWN"])
async def test_only_executor_resolves_unsent_or_ambiguous_attempt(test_db_url, state):
    async with Database(test_db_url) as db:
        async with db.tx() as conn:
            await seed(conn, state)
        reader = Reader()
        result = await poll(db, reader)
        assert getattr(result, state.lower()) == 1
        assert result.seen == 0 and not reader.calls


@pytest.mark.parametrize("corruption", ["receiver", "amount", "batch", "items", "item_id"])
async def test_identity_mismatch_never_changes_domain_or_ledger(test_db_url, corruption):
    async with Database(test_db_url) as db:
        async with db.tx() as conn:
            attempt, line, obligation, _ = await seed(conn)
        reader = Reader("SUCCESS")
        if corruption == "receiver":
            reader.detail["payout_item"]["receiver"] = "foreign@example.test"
        elif corruption == "amount":
            reader.detail["payout_item"]["amount"]["value"] = "10.01"
        elif corruption == "batch":
            reader.detail["payout_batch_id"] = "OTHER"
        elif corruption == "item_id":
            reader.detail["payout_item_id"] = None
        else:
            reader.batch_override = {
                "batch_header": {
                    "payout_batch_id": "BATCH-1",
                    "sender_batch_header": {"sender_batch_id": "sender-batch"},
                },
                "items": [reader.doc(), reader.doc()],
            }
        result = await poll(db, reader)
        assert result.errors == result.incidents == 1
        assert await states(db, attempt, line, obligation) == ("SUBMITTED", "PAYING", "IN_FLIGHT")
        async with db.tx() as conn:
            assert len(await Repositories(conn).ledger_journals.find()) == 1


async def test_atomic_cas_failure_rolls_back_mirror_and_journal(test_db_url, monkeypatch):
    async with Database(test_db_url) as db:
        async with db.tx() as conn:
            attempt, line, obligation, _ = await seed(conn, item_id="ITEM-1")

        async def failed(*args, **kwargs):
            raise RuntimeError("private driver details")

        monkeypatch.setattr("countersign.reconcile.attempts.cas_transition", failed)
        result = await poll(db, Reader("SUCCESS"))
        assert result.errors == 1
        assert await states(db, attempt, line, obligation) == ("SUBMITTED", "PAYING", "IN_FLIGHT")
        async with db.tx() as conn:
            repos = Repositories(conn)
            assert len(await repos.ledger_journals.find()) == 1
            assert "private" not in str(await repos.incidents.find())


async def test_unknown_status_keeps_raw_observation_without_financial_change(test_db_url):
    async with Database(test_db_url) as db:
        async with db.tx() as conn:
            attempt, line, obligation, _ = await seed(conn, item_id="ITEM-1")
        result = await poll(db, Reader("NEW_PAYPAL_STATE"))
        assert result.incidents == 1
        assert await states(db, attempt, line, obligation) == ("SUBMITTED", "PAYING", "IN_FLIGHT")
        async with db.tx() as conn:
            raw = (await Repositories(conn).payout_attempts.get(attempt["id"]))["paypal_raw"]
            assert raw["transaction_status"] == "NEW_PAYPAL_STATE"


async def test_old_attempt_cannot_settle_replacement_obligation(test_db_url):
    async with Database(test_db_url) as db:
        async with db.tx() as conn:
            attempt, line, obligation, _ = await seed(conn, item_id="ITEM-1")
        reader = Reader("RETURNED")
        await poll(db, reader)
        async with db.tx() as conn:
            repos = Repositories(conn)
            await repos.payout_lines.update(line["id"], state="PAYING")
            await repos.obligations.update(obligation["id"], state="IN_FLIGHT")
            operation = await repos.operations.insert(
                kind="PAYOUT_CREATE",
                target_type="payout_line",
                target_id=line["id"],
                payload={},
                payload_hash=b"y" * 32,
                state="UNKNOWN",
            )
            await repos.payout_attempts.insert(
                line_id=line["id"],
                attempt_no=2,
                operation_id=operation["id"],
                sender_batch_id="sender-batch-2",
                request_id="request-2",
                sender_item_id="item-2",
                payee_email="maker@example.test",
                amount_minor=1000,
                currency="USD",
                state="UNKNOWN",
            )
        initial = await totals(db)
        reader.status = "SUCCESS"
        await poll(db, reader)
        assert await states(db, attempt, line, obligation) == ("SUCCESS", "PAYING", "IN_FLIGHT")
        assert await totals(db) == initial
        reader.status = "PENDING"
        result = await poll(db, reader)
        assert result.errors == 0
        async with db.tx() as conn:
            old = await Repositories(conn).payout_attempts.get(attempt["id"])
            assert (
                old["state"] == "SUCCESS" and old["paypal_raw"]["transaction_status"] == "PENDING"
            )


async def test_allocation_mismatch_is_mirrored_but_cannot_settle(test_db_url):
    async with Database(test_db_url) as db:
        async with db.tx() as conn:
            attempt, line, obligation, _ = await seed(conn, item_id="ITEM-1")
            await conn.execute("UPDATE payout_line_obligations SET amount_minor=900")
        result = await poll(db, Reader("SUCCESS"))
        assert result.incidents == 1 and result.errors == 0
        assert await states(db, attempt, line, obligation) == ("SUCCESS", "PAYING", "IN_FLIGHT")
        async with db.tx() as conn:
            assert len(await Repositories(conn).ledger_journals.find()) == 1


async def test_http_failure_retains_retryable_attempt_and_safe_incident(test_db_url):
    class FailingReader(Reader):
        async def get_payout_item(self, resource_id):
            return SimpleNamespace(status_code=503, body={"message": "private"})

    async with Database(test_db_url) as db:
        async with db.tx() as conn:
            attempt, line, obligation, _ = await seed(conn, item_id="ITEM-1")
        result = await poll(db, FailingReader())
        assert result.errors == result.incidents == 1
        assert await states(db, attempt, line, obligation) == ("SUBMITTED", "PAYING", "IN_FLIGHT")
        async with db.tx() as conn:
            assert "private" not in str(await Repositories(conn).incidents.find())


async def test_missing_submission_journal_does_not_invent_financial_history(test_db_url):
    async with Database(test_db_url) as db:
        async with db.tx() as conn:
            attempt, line, obligation, _ = await seed(conn, item_id="ITEM-1")
            # Simulate inconsistent imported pre-C19 data; proper execution always posts it.
            await conn.execute("DELETE FROM ledger_postings")
            await conn.execute("DELETE FROM ledger_journals")
        result = await poll(db, Reader("SUCCESS"))
        assert result.incidents == 1 and result.errors == 0
        assert await states(db, attempt, line, obligation) == ("SUCCESS", "PAYING", "IN_FLIGHT")
        assert await totals(db) == {}
