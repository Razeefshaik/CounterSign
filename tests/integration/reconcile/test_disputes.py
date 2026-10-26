"""Dispute safety through fresh GETs, including complete webhook loss."""

import asyncio
from copy import deepcopy
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

from countersign.db import Database, Repositories
from countersign.fsm.dispute import OUTCOME_MAP, Phase
from countersign.ledger.journal import serialize_on
from countersign.ledger.reservations import earmark_dispute_share
from countersign.money import Money
from countersign.reconcile.disputes import ingest_dispute, reconcile_disputes

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
NOW = datetime(2026, 11, 6, 10, tzinfo=UTC)
OUTCOMES = {
    "SYNTHETIC_WON": Phase.WON,
    "SYNTHETIC_LOSS": Phase.LOST_FULL,
    "SYNTHETIC_CANCEL": Phase.CANCELLED,
}


class Reader:
    def __init__(self, *, discovery=False):
        self.calls = []
        self.discovery = discovery
        self.status_code = 200
        self.detail = {
            "dispute_id": "DISPUTE-1",
            "status": "UNDER_REVIEW",
            "dispute_life_cycle_stage": "CHARGEBACK",
            "reason": "OTHER",
            "dispute_amount": {"value": "400.00", "currency_code": "USD"},
            "disputed_transactions": [{"seller_transaction_id": "CAPTURE-1"}],
            "links": [{"rel": "self", "href": "http://127.0.0.1:8099/dispute-1", "method": "GET"}],
        }

    def resolve(self, code, refund=None):
        self.detail["status"] = "RESOLVED"
        self.detail["dispute_outcome"] = {"outcome_code": code}
        if refund is not None:
            self.detail["dispute_outcome"]["amount_refunded"] = {
                "value": refund,
                "currency_code": "USD",
            }

    async def get_dispute(self, identifier):
        self.calls.append(identifier)
        return SimpleNamespace(status_code=self.status_code, body=deepcopy(self.detail))

    async def list_disputes(self, **params):
        return SimpleNamespace(
            status_code=200,
            body={
                "items": [{"dispute_id": "DISPUTE-1", "status": "UNTRUSTED_LIST_STATUS"}]
                if self.discovery
                else [],
                "links": [],
            },
        )


async def seed(db, *, state="RESERVED", dispute=True, earmark=True):
    async with db.tx() as conn:
        repos = Repositories(conn)
        maker = await repos.makers.insert(display_name="Maker", payout_email="maker@example.test")
        order = await repos.orders.insert(
            maker_id=maker["id"],
            buyer_ref="buyer-1",
            amount_minor=40000,
            currency="USD",
            paypal_capture_id="CAPTURE-1",
        )
        obligation = await repos.obligations.insert(
            maker_id=maker["id"],
            order_id=order["id"],
            kind="ORDER_SHARE",
            amount_minor=36000,
            currency="USD",
            due_date=date(2026, 11, 6),
            state=state,
        )
        await repos.policies.insert(key="maker_share_pct", value=90)
        row = None
        if dispute:
            row = await repos.disputes.insert(
                paypal_dispute_id="DISPUTE-1",
                order_id=order["id"],
                status="OPEN",
                phase="ACTIVE",
                amount_minor=40000,
                currency="USD",
            )
            if earmark:
                await earmark_dispute_share(
                    conn,
                    dispute_id=row["id"],
                    obligation_id=obligation["id"],
                    order_amount=Money(40000, "USD"),
                    maker_share_pct=90,
                )
        return order, obligation, row


async def run(db, reader, *, outcomes=OUTCOMES):
    async with db.tx() as conn:
        await serialize_on(conn, "countersign:owner-decisions")
        return await reconcile_disputes(conn, reader, now=NOW, outcome_map=outcomes)


async def rows(db, table):
    async with db.tx() as conn:
        return await Repositories(conn)[table].find()


@pytest.mark.parametrize("initial", ["OPEN", "SCHEDULED"])
async def test_poll_protects_share_without_any_webhook(test_db_url, initial):
    async with Database(test_db_url) as db:
        await seed(db, state=initial, earmark=False)
        reader = Reader()
        first = await run(db, reader)
        first_dispute = (await rows(db, "disputes"))[0]
        assert first.seen == 1 and first.errors == 0
        assert first_dispute["status"] == "UNDER_REVIEW"
        assert first_dispute["lifecycle_stage"] == "CHARGEBACK"
        assert first_dispute["links"] == reader.detail["links"]
        obligation = (await rows(db, "obligations"))[0]
        assert obligation["state"] == "RESERVED" and obligation["version"] == 2
        marks = await rows(db, "reservations")
        assert len(marks) == 1 and marks[0]["affects_cash"] is False
        second = await run(db, reader)
        assert second.updated == 0
        assert (await rows(db, "disputes"))[0]["version"] == first_dispute["version"]
        assert len(await rows(db, "reservations")) == 1


@pytest.mark.parametrize("code", ["SYNTHETIC_WON", "RESOLVED_SELLER_FAVOUR"])
async def test_won_releases_earmark_and_opens_obligation_once(test_db_url, code):
    async with Database(test_db_url) as db:
        await seed(db)
        reader = Reader()
        reader.resolve(code)
        result = await run(db, reader, outcomes=None if code.startswith("RESOLVED_") else OUTCOMES)
        assert result.errors == 0 and result.updated == 1
        assert (await rows(db, "obligations"))[0]["state"] == "OPEN"
        assert (await rows(db, "reservations"))[0]["state"] == "RELEASED"
        assert (await rows(db, "disputes"))[0]["phase"] == "WON"
        assert (await run(db, reader)).seen == 0
        audits = await rows(db, "audit_log")
        assert (
            len(
                [
                    r
                    for r in audits
                    if r["kind"] == "STATE_TRANSITION"
                    and r["details"].get("event") == "DISPUTE_FINANCIAL_RESOLUTION"
                ]
            )
            == 1
        )


async def test_unknown_outcome_keeps_earmark_and_deduplicates_incident(test_db_url):
    async with Database(test_db_url) as db:
        await seed(db)
        reader = Reader()
        reader.resolve("NOT_VERIFIED_IN_D10")
        first = await run(db, reader, outcomes=None)
        second = await run(db, reader, outcomes=None)
        assert "NOT_VERIFIED_IN_D10" not in OUTCOME_MAP
        assert first.unknown == second.unknown == 1
        assert first.incidents == 1 and second.incidents == 0
        assert (await rows(db, "obligations"))[0]["state"] == "RESERVED"
        assert (await rows(db, "reservations"))[0]["state"] == "ACTIVE"
        assert (await rows(db, "disputes"))[0]["phase"] == "UNKNOWN_OUTCOME"


async def test_cancelled_only_releases_its_own_earmark(test_db_url):
    async with Database(test_db_url) as db:
        order, obligation, _ = await seed(db)
        async with db.tx() as conn:
            other = await Repositories(conn).disputes.insert(
                paypal_dispute_id="DISPUTE-2", order_id=order["id"], status="OPEN", phase="ACTIVE"
            )
            await earmark_dispute_share(
                conn,
                dispute_id=other["id"],
                obligation_id=obligation["id"],
                order_amount=Money(40000, "USD"),
                maker_share_pct=90,
            )
        reader = Reader()
        reader.resolve("SYNTHETIC_CANCEL")
        result = await run(db, reader)
        assert result.errors == 1  # reader deliberately rejects the second resource's identity
        assert (await rows(db, "obligations"))[0]["state"] == "RESERVED"
        assert sorted(r["state"] for r in await rows(db, "reservations")) == ["ACTIVE", "RELEASED"]


@pytest.mark.parametrize("code", ["SYNTHETIC_LOSS", "RESOLVED_BUYER_FAVOUR"])
async def test_full_loss_voids_and_consumes_without_guessing_refund_funding(test_db_url, code):
    async with Database(test_db_url) as db:
        await seed(db)
        reader = Reader()
        reader.resolve(code, "400.00")
        result = await run(db, reader, outcomes=None if code.startswith("RESOLVED_") else OUTCOMES)
        assert result.errors == 0
        assert (await rows(db, "obligations"))[0]["state"] == "VOID"
        assert (await rows(db, "reservations"))[0]["state"] == "CONSUMED"
        assert not await rows(db, "ledger_journals")
        assert any(
            r["subject"]["reason"] == "REFUND_FUNDING_UNVERIFIED"
            for r in await rows(db, "incidents")
        )


@pytest.mark.parametrize("code", ["SYNTHETIC_LOSS", "RESOLVED_BUYER_FAVOUR"])
async def test_partial_loss_has_one_negative_adjustment_and_atomic_resolution_marker(
    test_db_url, code
):
    async with Database(test_db_url) as db:
        _, obligation, dispute = await seed(db)
        reader = Reader()
        reader.resolve(code, "10.05")
        outcomes = None if code.startswith("RESOLVED_") else OUTCOMES
        assert (await run(db, reader, outcomes=outcomes)).errors == 0
        obligations = await rows(db, "obligations")
        original = next(r for r in obligations if r["kind"] == "ORDER_SHARE")
        adjustment = next(r for r in obligations if r["kind"] == "ADJUSTMENT")
        assert original["state"] == "OPEN" and original["amount_minor"] == 36000
        assert adjustment["amount_minor"] == -905  # 1005 * 90% = 904.5 -> half-up
        assert adjustment["due_date"] == obligation["due_date"]
        assert (await rows(db, "reservations"))[0]["state"] == "CONSUMED"
        assert (await run(db, reader, outcomes=outcomes)).seen == 0
        # A later stale webhook may put the mirror back in the polling set.
        async with db.tx() as conn:
            await Repositories(conn).disputes.update(dispute["id"], phase="UNKNOWN_OUTCOME")
        assert (await run(db, reader, outcomes=outcomes)).errors == 0
        assert len(await rows(db, "obligations")) == 2
        assert len(await rows(db, "reservations")) == 1
        audits = [
            r
            for r in await rows(db, "audit_log")
            if r["kind"] == "STATE_TRANSITION"
            and r["details"].get("event") == "DISPUTE_FINANCIAL_RESOLUTION"
        ]
        assert len(audits) == 1 and audits[0]["details"]["adjustment_id"] == str(adjustment["id"])


async def test_partial_resolution_crash_rolls_every_financial_change_back(test_db_url, monkeypatch):
    async with Database(test_db_url) as db:
        await seed(db)
        reader = Reader()
        reader.resolve("SYNTHETIC_LOSS", "100.00")
        from countersign.reconcile import disputes

        real_consume = disputes.consume_reservation

        async def fail_after_adjustment(*args):
            raise RuntimeError("private failure after insert")

        monkeypatch.setattr(disputes, "consume_reservation", fail_after_adjustment)
        result = await run(db, reader)
        assert result.errors == 1
        assert len(await rows(db, "obligations")) == 1
        assert (await rows(db, "obligations"))[0]["state"] == "RESERVED"
        assert (await rows(db, "disputes"))[0]["phase"] == "ACTIVE"
        assert (await rows(db, "reservations"))[0]["state"] == "ACTIVE"
        monkeypatch.setattr(disputes, "consume_reservation", real_consume)
        assert (await run(db, reader)).errors == 0
        assert len(await rows(db, "obligations")) == 2


async def test_existing_adjustment_is_never_overwritten(test_db_url):
    async with Database(test_db_url) as db:
        order, obligation, _ = await seed(db, state="OPEN", earmark=False)
        async with db.tx() as conn:
            await Repositories(conn).obligations.insert(
                maker_id=order["maker_id"],
                order_id=order["id"],
                kind="ADJUSTMENT",
                amount_minor=-123,
                currency="USD",
                due_date=obligation["due_date"],
                state="OPEN",
            )
        reader = Reader()
        reader.resolve("SYNTHETIC_LOSS", "100.00")
        result = await run(db, reader)
        assert result.errors == 0 and result.incidents == 1
        obligations = await rows(db, "obligations")
        assert next(r for r in obligations if r["kind"] == "ORDER_SHARE")["state"] == "RESERVED"
        assert next(r for r in obligations if r["kind"] == "ADJUSTMENT")["amount_minor"] == -123
        assert (await rows(db, "reservations"))[0]["state"] == "ACTIVE"


@pytest.mark.parametrize("refund", ["401.00", "-1.00"])
async def test_invalid_loss_amounts_stay_unknown_and_protected(test_db_url, refund):
    async with Database(test_db_url) as db:
        await seed(db)
        reader = Reader()
        reader.resolve("SYNTHETIC_LOSS", refund)
        assert (await run(db, reader)).unknown == 1
        assert (await rows(db, "obligations"))[0]["state"] == "RESERVED"
        assert (await rows(db, "reservations"))[0]["state"] == "ACTIVE"


async def test_capture_identity_mismatch_never_adopts_another_dispute(test_db_url):
    async with Database(test_db_url) as db:
        await seed(db)
        reader = Reader()
        reader.detail["disputed_transactions"] = [{"seller_transaction_id": "OTHER-CAPTURE"}]
        assert (await run(db, reader)).errors == 1
        assert (await rows(db, "disputes"))[0]["status"] == "OPEN"
        assert (await rows(db, "reservations"))[0]["state"] == "ACTIVE"


async def test_discovery_gets_new_dispute_and_protects_without_webhook(test_db_url):
    async with Database(test_db_url) as db:
        await seed(db, state="OPEN", dispute=False, earmark=False)
        reader = Reader(discovery=True)
        result = await run(db, reader)
        assert result.seen == result.updated == 1 and result.errors == 0
        assert reader.calls == ["DISPUTE-1"]
        assert (await rows(db, "disputes"))[0]["status"] == "UNDER_REVIEW"
        assert (await rows(db, "obligations"))[0]["state"] == "RESERVED"


async def test_concurrent_cycles_release_once_under_shared_owner_lock(test_db_url):
    async with Database(test_db_url) as db:
        await seed(db)
        reader = Reader()
        reader.resolve("SYNTHETIC_WON")
        results = await asyncio.gather(run(db, reader), run(db, reader))
        assert sum(r.updated for r in results) == 1
        assert (await rows(db, "obligations"))[0]["version"] == 2
        assert (await rows(db, "reservations"))[0]["state"] == "RELEASED"


async def test_backfill_ingestion_skips_foreign_capture(test_db_url):
    async with Database(test_db_url) as db:
        reader = Reader()
        async with db.tx() as conn:
            await serialize_on(conn, "countersign:owner-decisions")
            result = await ingest_dispute(conn, reader.detail, now=NOW)
        assert result.skipped == 1
        assert not await rows(db, "disputes")


async def test_discovery_follows_pages_and_gets_every_new_identity(test_db_url):
    class PaginatedReader(Reader):
        async def list_disputes(self, **params):
            return SimpleNamespace(
                status_code=200,
                body={
                    "items": [{"dispute_id": "DISPUTE-1"}],
                    "links": [
                        {"rel": "next", "href": "http://127.0.0.1:8099/next", "method": "GET"}
                    ],
                },
            )

        async def follow(self, href):
            assert href == "http://127.0.0.1:8099/next"
            return SimpleNamespace(
                status_code=200,
                body={
                    "items": [{"dispute_id": "DISPUTE-2"}],
                    "links": [],
                },
            )

        async def get_dispute(self, identifier):
            self.calls.append(identifier)
            return SimpleNamespace(
                status_code=200, body={**deepcopy(self.detail), "dispute_id": identifier}
            )

    async with Database(test_db_url) as db:
        await seed(db, state="OPEN", dispute=False, earmark=False)
        reader = PaginatedReader()
        result = await run(db, reader)
        assert result.seen == result.updated == 2 and result.errors == 0
        assert reader.calls == ["DISPUTE-1", "DISPUTE-2"]
        assert len(await rows(db, "reservations")) == 2
        assert (await rows(db, "obligations"))[0]["state"] == "RESERVED"


async def test_terminal_webhook_mirror_still_gets_its_earmark_resolved(test_db_url):
    async with Database(test_db_url) as db:
        _, _, dispute = await seed(db)
        async with db.tx() as conn:
            await Repositories(conn).disputes.update(dispute["id"], phase="WON", status="RESOLVED")
        reader = Reader()
        reader.resolve("SYNTHETIC_WON")
        assert (await run(db, reader)).seen == 1
        assert (await rows(db, "reservations"))[0]["state"] == "RELEASED"
        assert (await rows(db, "obligations"))[0]["state"] == "OPEN"


async def test_currency_mismatch_is_mirrored_but_cannot_release_reserve(test_db_url):
    async with Database(test_db_url) as db:
        await seed(db)
        reader = Reader()
        reader.resolve("SYNTHETIC_WON")
        reader.detail["dispute_amount"]["currency_code"] = "EUR"
        result = await run(db, reader)
        assert result.unknown == 1 and result.errors == 0
        dispute = (await rows(db, "disputes"))[0]
        assert dispute["currency"] == "EUR" and dispute["phase"] == "UNKNOWN_OUTCOME"
        assert (await rows(db, "reservations"))[0]["state"] == "ACTIVE"
        assert (await rows(db, "obligations"))[0]["state"] == "RESERVED"


async def test_missing_share_policy_still_blocks_open_obligation(test_db_url):
    async with Database(test_db_url) as db:
        await seed(db, state="OPEN", earmark=False)
        async with db.tx() as conn:
            await Repositories(conn).policies.delete("maker_share_pct")
        result = await run(db, Reader())
        assert result.errors == 0 and result.incidents == 1
        assert (await rows(db, "obligations"))[0]["state"] == "RESERVED"


@pytest.mark.parametrize("state", ["IN_FLIGHT", "SETTLED"])
async def test_loss_after_payout_never_guesses_a_clawback(test_db_url, state):
    async with Database(test_db_url) as db:
        await seed(db, state=state, earmark=False)
        reader = Reader()
        reader.resolve("SYNTHETIC_LOSS", "50.00")
        result = await run(db, reader)
        assert result.errors == 0
        assert (await rows(db, "obligations"))[0]["state"] == state
        assert len(await rows(db, "obligations")) == 1
        assert not await rows(db, "ledger_journals")
        assert {r["subject"]["reason"] for r in await rows(db, "incidents")} == {
            "PAID_BEFORE_DISPUTE",
            "REFUND_REQUIRES_OWNER",
        }


async def test_changed_resolution_protects_previously_reopened_share(test_db_url):
    async with Database(test_db_url) as db:
        await seed(db)
        reader = Reader()
        reader.resolve("SYNTHETIC_WON")
        assert (await run(db, reader)).errors == 0
        assert (await rows(db, "obligations"))[0]["state"] == "OPEN"
        reader.resolve("SYNTHETIC_LOSS", "100.00")
        async with db.tx() as conn:
            await serialize_on(conn, "countersign:owner-decisions")
            result = await ingest_dispute(conn, reader.detail, now=NOW, outcome_map=OUTCOMES)
        assert result.incidents == 1
        assert (await rows(db, "obligations"))[0]["state"] == "RESERVED"
        assert len(await rows(db, "obligations")) == 1
        assert any(r["state"] == "ACTIVE" for r in await rows(db, "reservations"))
