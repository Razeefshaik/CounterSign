"""Keep stale cash from funding a new payout after an unjournaled buyer refund."""

from datetime import datetime

from countersign.db import Repositories

PREFIX = "reconcile.refund:"


async def defer_refund_cash(conn, dispute, *, now):
    """Record observation once, without choosing withheld versus available [pending D10]."""
    amount = dispute["amount_refunded_minor"]
    if amount is None or amount <= 0:
        return
    repos = Repositories(conn)
    key = PREFIX + str(dispute["id"])
    previous = await repos.policies.get(key)
    currency = dispute["currency"].strip()
    if previous and (previous["value"]["amount_minor"], previous["value"]["currency"]) == (
        amount,
        currency,
    ):
        return
    value = {
        "amount_minor": amount,
        "currency": currency,
        "observed_at": now.isoformat(),
        "pending": True,
    }
    if previous:
        await repos.policies.update(key, value=value)
    else:
        await repos.policies.insert(key=key, value=value)
    cursor = await conn.execute(
        "SELECT * FROM balance_snapshots WHERE currency=%s "
        "ORDER BY fetched_at DESC,id DESC LIMIT 1",
        (currency,),
    )
    balance = await cursor.fetchone()
    # Preserve the original observation. The latest row explicitly carries unknown
    # spendable cash, even if the subsequent reporting API call fails this cycle.
    await repos.balance_snapshots.insert(
        fetched_at=now,
        currency=currency,
        available_minor=None,
        withheld_minor=balance["withheld_minor"] if balance else None,
        total_minor=balance["total_minor"] if balance else None,
        as_of_time=balance["as_of_time"] if balance else None,
        last_refresh_time=balance["last_refresh_time"] if balance else None,
        raw={
            "reconciliation": "REFUND_FUNDING_UNVERIFIED",
            "previous_snapshot_id": str(balance["id"]) if balance else None,
        },
    )


async def guard_balance(conn, values):
    """Clear pending refunds only when both reporting timestamps cover their observation."""
    cursor = await conn.execute(
        "SELECT key,value FROM policies WHERE starts_with(key,%s) "
        "AND value->>'pending'='true' ORDER BY key FOR UPDATE",
        (PREFIX,),
    )
    unknown = False
    for row in await cursor.fetchall():
        value = row["value"]
        if value["currency"] != values["currency"]:
            continue
        observed = datetime.fromisoformat(value["observed_at"])
        covered = all(
            values[field] is not None and values[field] >= observed
            for field in ("as_of_time", "last_refresh_time")
        )
        if covered:
            await Repositories(conn).policies.update(row["key"], value={**value, "pending": False})
        else:
            unknown = True
    if unknown:
        values["available_minor"] = None
