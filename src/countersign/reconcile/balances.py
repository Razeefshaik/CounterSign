"""Record exact balance observations; unknown amounts never become spendable zeroes.

The balance endpoint can lag [pending D12]. A snapshot is a cross-check with its
own freshness, never proof that an individual payout or dispute has completed.
"""

from datetime import datetime

from countersign.db import Repositories
from countersign.money import Money
from countersign.paypal.http import PayPalError

from .cash_guard import guard_balance
from .common import ReconcileError, StageResult


def _timestamp(value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError
    return parsed


def _amount(entry, key, currency):
    value = entry.get(key)
    if value is None:
        return None
    amount = Money.from_paypal(value)
    if amount.currency != currency:
        raise ValueError
    return amount.minor


def _observations(body, now):
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError
    if not isinstance(body, dict) or not isinstance(body.get("balances"), list):
        raise TypeError
    as_of = _timestamp(body.get("as_of_time"))
    refreshed = _timestamp(body.get("last_refresh_time"))
    rows, currencies = [], set()
    for entry in body["balances"]:
        if not isinstance(entry, dict):
            raise TypeError
        currency = entry.get("currency")
        Money.zero(currency)
        if currency in currencies:
            raise ValueError
        currencies.add(currency)
        available = _amount(entry, "available_balance", currency)
        withheld = _amount(entry, "withheld_balance", currency)
        total = _amount(entry, "total_balance", currency)
        if None not in (available, withheld, total) and available + withheld != total:
            raise ValueError
        rows.append(
            {
                "fetched_at": now,
                "as_of_time": as_of,
                "last_refresh_time": refreshed,
                "currency": currency,
                "available_minor": available,
                "withheld_minor": withheld,
                "total_minor": total,
            }
        )
    return rows


async def reconcile_balances(conn, reader, *, now: datetime, clock=None) -> StageResult:
    """Append an atomic observation in the caller's locked transaction.

    Parse *every* currency before inserting anything. Identical refreshes reuse the
    latest observation, while missing nullable fields remain genuinely unknown.
    """
    try:
        response = await reader.get_balances()
        if not 200 <= response.status_code < 300:
            raise ValueError
        rows = _observations(response.body, clock() if clock else now)
    except (ValueError, TypeError, AttributeError, PayPalError):
        raise ReconcileError("Invalid balance observation") from None
    result = StageResult(seen=len(rows))
    repos = Repositories(conn)
    for values in rows:
        await guard_balance(conn, values)
        cursor = await conn.execute(
            "SELECT * FROM balance_snapshots WHERE currency=%s "
            "ORDER BY fetched_at DESC,id DESC LIMIT 1",
            (values["currency"],),
        )
        previous = await cursor.fetchone()
        compared = tuple(key for key in values if key != "fetched_at")
        if previous and all(previous[key] == values[key] for key in compared):
            result.skipped += 1
            continue
        # A delayed response must not supersede a newer known refresh in cash.py.
        old_refresh = previous and previous["last_refresh_time"]
        new_refresh = values["last_refresh_time"]
        if old_refresh and new_refresh and new_refresh < old_refresh:
            result.skipped += 1
            continue
        await repos.balance_snapshots.insert(
            **values, raw=getattr(response, "safe_body", None) or {}
        )
        result.updated += 1
    return result
