"""A partial loss cannot turn a stale reported balance or the gross share into a payout."""

from datetime import UTC, date, datetime, timedelta

import pytest

from countersign.approvals import ApprovalError, ApprovalService
from countersign.db import Database, Repositories
from countersign.fsm.dispute import Phase
from countersign.paypal.http import HTTPResponse
from countersign.reconcile.balances import reconcile_balances
from countersign.reconcile.cash_guard import PREFIX
from countersign.reconcile.disputes import ingest_dispute

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
NOW = datetime(2026, 11, 6, 9, tzinfo=UTC)


class BalanceReader:
    def __init__(self, refreshed):
        self.refreshed = refreshed

    async def get_balances(self):
        timestamp = self.refreshed.isoformat()
        return HTTPResponse(
            200,
            {
                "as_of_time": timestamp,
                "last_refresh_time": timestamp,
                "balances": [
                    {
                        "currency": "USD",
                        "available_balance": {"currency_code": "USD", "value": "900.00"},
                    }
                ],
            },
        )


async def test_partial_refund_blocks_stale_cash_then_plans_only_net_share(test_db_url):
    async with Database(test_db_url) as database:
        async with database.tx() as conn:
            repos = Repositories(conn)
            maker = await repos.makers.insert(
                display_name="Maker",
                payout_email="maker@example.test",
                payout_email_verified_at=NOW - timedelta(days=1),
            )
            order = await repos.orders.insert(
                maker_id=maker["id"],
                buyer_ref="buyer",
                amount_minor=40000,
                currency="USD",
                paypal_capture_id="CAP-PARTIAL",
            )
            await repos.obligations.insert(
                maker_id=maker["id"],
                order_id=order["id"],
                kind="ORDER_SHARE",
                amount_minor=36000,
                currency="USD",
                due_date=date(2026, 11, 6),
                state="OPEN",
            )
            await repos.policies.insert(key="maker_share_pct", value=90)
            await reconcile_balances(conn, BalanceReader(NOW - timedelta(hours=1)), now=NOW)
        detail = {
            "dispute_id": "PP-D-PARTIAL",
            "status": "RESOLVED",
            "disputed_transactions": [{"seller_transaction_id": "CAP-PARTIAL"}],
            "dispute_amount": {"currency_code": "USD", "value": "400.00"},
            "dispute_outcome": {
                "outcome_code": "SYNTHETIC_LOSS",
                "amount_refunded": {"currency_code": "USD", "value": "60.00"},
            },
        }
        async with database.tx() as conn:
            result = await ingest_dispute(
                conn, detail, now=NOW, outcome_map={"SYNTHETIC_LOSS": Phase.LOST_FULL}
            )
            assert result.errors == 0
        service = ApprovalService(database, clock=lambda: NOW)
        with pytest.raises(ApprovalError, match="CASH_UNKNOWN"):
            await service.plan_run(date(2026, 11, 2))
        async with database.tx() as conn:
            await reconcile_balances(conn, BalanceReader(NOW - timedelta(hours=1)), now=NOW)
        with pytest.raises(ApprovalError, match="CASH_UNKNOWN"):
            await service.plan_run(date(2026, 11, 2))
        async with database.tx() as conn:
            await reconcile_balances(
                conn, BalanceReader(NOW + timedelta(minutes=1)), now=NOW + timedelta(minutes=1)
            )
            # A repeated resource GET must not make the already covered refund pending again.
            await ingest_dispute(
                conn,
                detail,
                now=NOW + timedelta(minutes=2),
                outcome_map={"SYNTHETIC_LOSS": Phase.LOST_FULL},
            )
            policies = await Repositories(conn).policies.find()
            pending = [p["value"]["pending"] for p in policies if p["key"].startswith(PREFIX)]
            assert pending == [False]
        run = await service.plan_run(date(2026, 11, 2))
        async with database.tx() as conn:
            repos = Repositories(conn)
            lines = await repos.payout_lines.find({"run_id": run["id"]})
            assert [line["amount_minor"] for line in lines] == [30600]
            shares = await repos.payout_line_obligations.find({"line_id": lines[0]["id"]})
            assert sorted(row["amount_minor"] for row in shares) == [-5400, 36000]
