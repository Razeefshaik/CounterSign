"""The real read adapter and poller recover a payout without any delivered webhook."""

from datetime import date

import httpx
import pytest

from countersign.config import PayPalSettings
from countersign.db import Database, Repositories
from countersign.ledger import journal
from countersign.money import Money
from countersign.paypal.http import PayPalHTTP, canonical_payload_hash
from countersign.paypal.read import PayPalRead
from countersign.reconcile import Reconciler
from countersign.shield.rules.idempotency import derive_keys
from mock_paypal.app import create_app

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_real_mock_and_read_adapter_settle_once_without_webhook_delivery(
    test_db_url, poll_until_ready
):
    mock = create_app(control_enabled=True)
    transport = httpx.ASGITransport(app=mock)
    base = "http://localhost:8099"
    async with (
        Database(test_db_url) as database,
        httpx.AsyncClient(transport=transport, base_url=base) as controls,
        PayPalHTTP(PayPalSettings(base_url=base), transport=transport) as http,
    ):
        assert (
            await controls.post(
                "/_mock/seed",
                json={
                    "balances": {"available": "100.00"},
                    "registered_receivers": ["maker@example.test"],
                },
            )
        ).status_code == 200
        assert (
            await controls.post(
                "/_mock/webhook-faults",
                json={
                    "match": {"event_type": "PAYMENT.PAYOUTS-ITEM.SUCCEEDED"},
                    "action": {"type": "drop"},
                },
            )
        ).status_code == 201
        async with database.tx() as conn:
            repos = Repositories(conn)
            maker = await repos.makers.insert(
                display_name="Maker", payout_email="maker@example.test"
            )
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
                line_id=line["id"], obligation_id=obligation["id"], amount_minor=1000
            )
        keys = derive_keys(str(line["id"]), 1)
        body = {
            "sender_batch_header": {"sender_batch_id": keys.sender_batch_id},
            "items": [
                {
                    "recipient_type": "EMAIL",
                    "receiver": "maker@example.test",
                    "amount": {"value": "10.00", "currency": "USD"},
                    "sender_item_id": keys.sender_item_id,
                }
            ],
        }
        # Test setup only: the C15 implementation imports and calls GET-only PayPalRead.
        created = await http.request(
            "POST", "/v1/payments/payouts", json=body, request_id=keys.request_id
        )
        assert created.status_code == 201
        batch_id = created.body["batch_header"]["payout_batch_id"]
        now = lambda: mock.state.store.clock.now
        payload = {"body": body, "request_id": keys.request_id}
        async with database.tx() as conn:
            repos = Repositories(conn)
            operation = await repos.operations.insert(
                kind="PAYOUT_CREATE",
                target_type="payout_line",
                target_id=line["id"],
                payload=payload,
                payload_hash=bytes.fromhex(canonical_payload_hash(payload)[7:]),
                state="SUCCEEDED",
                submitted_at=now(),
            )
            attempt = await repos.payout_attempts.insert(
                line_id=line["id"],
                attempt_no=1,
                operation_id=operation["id"],
                sender_batch_id=keys.sender_batch_id,
                sender_item_id=keys.sender_item_id,
                request_id=keys.request_id,
                payee_email="maker@example.test",
                amount_minor=1000,
                currency="USD",
                state="SUBMITTED",
                paypal_batch_id=batch_id,
            )
            await journal.post_journal(
                conn,
                journal.payout_submitted(Money(1000, "USD")),
                operation_id=operation["id"],
                occurred_at=now(),
            )
        service = Reconciler(database, PayPalRead(http), clock=now)
        first = await poll_until_ready(service)
        assert first.attempts.errors == 0 and first.attempts.updated == 1
        async with database.tx() as conn:
            mirrored = await Repositories(conn).payout_attempts.get(attempt["id"])
            assert mirrored["state"] == "PENDING" and mirrored["paypal_item_id"]
        assert (
            await controls.post("/_mock/clock/advance", json={"seconds": 31})
        ).status_code == 200
        assert (await poll_until_ready(service)).attempts.errors == 0
        await poll_until_ready(service)
        async with database.tx() as conn:
            repos = Repositories(conn)
            assert (await repos.obligations.get(obligation["id"]))["state"] == "SETTLED"
            assert (await repos.payout_lines.get(line["id"]))["state"] == "PAID"
            assert not await repos.webhook_inbox.find()
            rows = await repos.ledger_journals.find({"operation_id": operation["id"]})
            assert sum(row["memo"].startswith("PAYOUT_SUCCESS:") for row in rows) == 1
        truth = (await controls.get("/_mock/ground-truth")).json()
        assert len(truth["payout_items"]) == 1
        assert truth["payout_items"][0]["status"] == "SUCCESS"
