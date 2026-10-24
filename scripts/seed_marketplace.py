"""Seed an empty local marketplace from scenario JSON; never overwrite owner records."""

import argparse
import asyncio
import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path

from countersign.config import load_settings
from countersign.db import Database, Repositories
from countersign.ledger.journal import serialize_on
from countersign.money import Money

DEFAULT_SCENARIO = Path(__file__).resolve().parents[1] / "eval/scenarios/demo_week.json"


async def seed_marketplace(database, scenario: dict) -> dict:
    """Create initial makers/orders and one ORDER_SHARE obligation per order atomically.

    A same-document rerun is a no-op even after the owner has changed records. An
    unmarked, nonempty database is refused rather than merged or reset.
    """
    digest = hashlib.sha256(
        json.dumps(scenario, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    initial = scenario["initial_state"]
    start = datetime.fromisoformat(scenario["start"])
    due = start.date() + timedelta(days=(4 - start.weekday()) % 7)
    share = initial["policy"]["maker_share_pct"]
    async with database.tx() as conn:
        await serialize_on(conn, "countersign:owner-decisions")
        repos = Repositories(conn)
        marker = await repos.policies.get("marketplace_seed")
        if marker:
            if marker["value"]["hash"] != digest:
                raise ValueError("A different scenario is already seeded")
            return {"created": False, **marker["value"]["counts"]}
        # An adjustment or a run may exist without an order. Refuse all existing
        # marketplace/history rows, including policies; ingress alone may predate seed.
        tables = (
            "makers",
            "orders",
            "obligations",
            "payout_runs",
            "payout_lines",
            "payout_attempts",
            "operations",
            "disputes",
            "reservations",
            "ledger_journals",
            "ledger_postings",
            "incidents",
            "proposals",
            "approvals",
            "balance_snapshots",
            "policies",
            "audit_log",
        )
        for table in tables:
            if await repos[table].find(limit=1):
                raise ValueError("Seed requires an empty marketplace")
        makers = {}
        for item in initial["makers"]:
            if item["key"] in makers:
                raise ValueError("Duplicate maker key")
            makers[item["key"]] = await repos.makers.insert(
                display_name=item["display_name"],
                payout_email=item.get("payout_email"),
                payout_email_verified_at=start if item.get("verified") else None,
            )
        for item in initial["orders"]:
            amount = Money(**item["amount"])
            obligation_amount = amount.share_pct(share)
            if obligation_amount.minor <= 0:
                raise ValueError("Order share must be positive")
            order = await repos.orders.insert(
                maker_id=makers[item["maker"]]["id"],
                buyer_ref=item["key"],
                amount_minor=amount.minor,
                currency=amount.currency,
                paypal_capture_id=item.get("capture_id"),
                paypal_order_id=item.get("paypal_order_id"),
                captured_at=datetime.fromisoformat(item["captured_at"]),
                tracking=item.get("tracking"),
            )
            await repos.obligations.insert(
                maker_id=order["maker_id"],
                order_id=order["id"],
                kind="ORDER_SHARE",
                amount_minor=obligation_amount.minor,
                currency=amount.currency,
                due_date=due,
                state="OPEN",
            )
        for key, value in {"deadline_close_seconds": 172800, **initial["policy"]}.items():
            await repos.policies.insert(key=key, value=value)
        counts = {
            "makers": len(makers),
            "orders": len(initial["orders"]),
            "obligations": len(initial["orders"]),
        }
        await repos.policies.insert(
            key="marketplace_seed", value={"hash": digest, "counts": counts}
        )
    return {"created": True, **counts}


async def _run(path):
    scenario = json.loads(path.read_text(encoding="utf-8"))
    async with Database(load_settings()) as database:
        result = await seed_marketplace(database, scenario)
    print(json.dumps(result))  # Counts only, never credentials, payees or resource contents.


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario", nargs="?", type=Path, default=DEFAULT_SCENARIO)
    args = parser.parse_args()
    try:
        asyncio.run(_run(args.scenario))
    except Exception:  # noqa: BLE001 - command boundary never prints private driver values.
        raise SystemExit("Marketplace seed failed; no partial seed was committed") from None


if __name__ == "__main__":
    # psycopg's async driver needs selectors on Windows, including Python 3.12.
    import sys

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    main()
