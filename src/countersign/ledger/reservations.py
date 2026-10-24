"""Reservations: money held back for a dispute, a pending proposal or an approved run.

A reservation is ACTIVE until it is RELEASED (no longer needed) or CONSUMED (the money it
protected was spent). The table has no version column, so each move is guarded by
`WHERE state = 'ACTIVE'` and raises when another writer got there first.

The schema has no unique index for "one active earmark per (dispute, obligation)", so
create_reservation enforces it with an advisory lock and a check (see journal.serialize_on).
"""

from uuid import UUID

from countersign.db import Repositories
from countersign.db.pool import Connection
from countersign.money import Money

from .cash import CASH_KINDS, EARMARK, Reservation
from .journal import serialize_on

KINDS = CASH_KINDS | {EARMARK}
_LINK = {"DISPUTE_EARMARK": "dispute_id", "PROPOSAL_CASH": "proposal_id", "RUN_CASH": "run_id"}


class ReservationError(RuntimeError):
    """A reservation is malformed, conflicts with an active one, or is no longer ACTIVE."""


class ReservationNotActive(ReservationError):
    """The reservation was already released or consumed, or does not exist."""


def dispute_earmark_amount(order_amount: Money, maker_share_pct: int) -> Money:
    """The maker's share of a disputed order (policy maker_share_pct; demo 90%)."""
    if not isinstance(order_amount, Money) or order_amount.minor <= 0:
        raise ReservationError("The disputed order amount must be positive Money.")
    return order_amount.share_pct(maker_share_pct)


async def create_reservation(
    conn: Connection,
    kind: str,
    amount: Money,
    *,
    obligation_id: UUID | None = None,
    dispute_id: UUID | None = None,
    proposal_id: UUID | None = None,
    run_id: UUID | None = None,
) -> dict:
    """Insert an ACTIVE reservation inside the caller's transaction; return its row.

    A dispute earmark needs both its dispute and the obligation it holds, and the
    obligation must belong to the disputed order. Repeating an earmark with the same
    amount returns the active one; a different amount raises.
    """
    if kind not in KINDS:
        raise ReservationError(f"Unknown reservation kind {kind!r}.")
    if not isinstance(amount, Money) or amount.minor <= 0:
        raise ReservationError("Reservation amounts are positive Money.")
    links = {
        "obligation_id": obligation_id,
        "dispute_id": dispute_id,
        "proposal_id": proposal_id,
        "run_id": run_id,
    }
    if links[_LINK[kind]] is None:
        raise ReservationError(f"A {kind} reservation needs {_LINK[kind]}.")
    repos = Repositories(conn)
    if kind == EARMARK:
        if obligation_id is None:
            raise ReservationError("A dispute earmark needs the obligation it holds.")
        await serialize_on(conn, f"countersign:earmark:{dispute_id}:{obligation_id}")
        cursor = await conn.execute(
            "SELECT 1 FROM obligations o JOIN disputes d ON d.order_id = o.order_id "
            "WHERE o.id = %s AND d.id = %s",
            (obligation_id, dispute_id),
        )
        if await cursor.fetchone() is None:
            raise ReservationError("The obligation is not on the disputed order.")
        active = await repos.reservations.find(
            {
                "kind": EARMARK,
                "dispute_id": dispute_id,
                "obligation_id": obligation_id,
                "state": "ACTIVE",
            }
        )
        if active:
            row = active[0]
            if (row["amount_minor"], row["currency"].strip()) != (amount.minor, amount.currency):
                raise ReservationError("An active earmark with a different amount exists.")
            return row
    return await repos.reservations.insert(
        kind=kind,
        amount_minor=amount.minor,
        currency=amount.currency,
        state="ACTIVE",
        **{column: value for column, value in links.items() if value is not None},
    )


async def earmark_dispute_share(
    conn: Connection,
    *,
    dispute_id: UUID,
    obligation_id: UUID,
    order_amount: Money,
    maker_share_pct: int,
) -> dict:
    """Earmark the maker's share of a disputed order (Maker A: 90% of $400 = $360)."""
    return await create_reservation(
        conn,
        EARMARK,
        dispute_earmark_amount(order_amount, maker_share_pct),
        obligation_id=obligation_id,
        dispute_id=dispute_id,
    )


async def _close(conn: Connection, reservation_id: UUID, state: str) -> dict:
    cursor = await conn.execute(
        "UPDATE reservations SET state = %s, closed_at = now() "
        "WHERE id = %s AND state = 'ACTIVE' RETURNING *",
        (state, reservation_id),
    )
    row = await cursor.fetchone()
    if row is None:
        raise ReservationNotActive("The reservation is not ACTIVE, or does not exist.")
    return row


async def release_reservation(conn: Connection, reservation_id: UUID) -> dict:
    """ACTIVE -> RELEASED: the money is no longer held back."""
    return await _close(conn, reservation_id, "RELEASED")


async def consume_reservation(conn: Connection, reservation_id: UUID) -> dict:
    """ACTIVE -> CONSUMED: the money it protected has been spent."""
    return await _close(conn, reservation_id, "CONSUMED")


async def load_active(conn: Connection, currency: str) -> list[Reservation]:
    """Every ACTIVE reservation in one currency, ready for cash.cash_position."""
    Money.zero(currency)
    cursor = await conn.execute(
        "SELECT kind, amount_minor, currency FROM reservations "
        "WHERE state = 'ACTIVE' AND currency = %s ORDER BY created_at, id",
        (currency,),
    )
    return [
        Reservation(row["kind"], Money(row["amount_minor"], row["currency"].strip()))
        for row in await cursor.fetchall()
    ]
