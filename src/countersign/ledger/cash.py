"""How much cash is really free to spend (PLAN 2.3 funding formula, F12).

PayPal's balance lags by up to three hours, so the formula stays conservative:

    effective_available = snapshot.available
                          - outflows we submitted after snapshot.last_refresh_time
    unreserved          = effective_available - active reservations that affect cash

Inflows count only once a snapshot reflects them, so they never appear here. A dispute
earmark never affects cash: PayPal already withholds that money [pending D8c, D12].
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from countersign.money import Money

CASH_KINDS = frozenset({"PROPOSAL_CASH", "RUN_CASH"})
EARMARK = "DISPUTE_EARMARK"


@dataclass(frozen=True)
class BalanceView:
    """One PayPal balance snapshot for a currency, as stored in balance_snapshots."""

    available: Money
    last_refresh_time: datetime | None
    withheld: Money | None = None


@dataclass(frozen=True)
class Outflow:
    """Money we sent out (payout attempt or refund), with when we submitted it."""

    amount: Money
    submitted_at: datetime


@dataclass(frozen=True)
class Reservation:
    """An ACTIVE row of the reservations table."""

    kind: str
    amount: Money

    def __post_init__(self):
        if self.kind not in CASH_KINDS | {EARMARK}:
            raise ValueError(f"Unknown reservation kind {self.kind!r}.")
        if self.amount.minor <= 0:
            raise ValueError("Reservation amounts are positive.")


@dataclass(frozen=True)
class CashPosition:
    available: Money
    outflows_after_refresh: Money
    effective_available: Money
    cash_reserved: Money
    earmarked: Money
    unreserved: Money


def cash_position(
    balance: BalanceView, outflows: Iterable[Outflow], reservations: Iterable[Reservation]
) -> CashPosition:
    """Apply the funding formula; every amount must share the balance's currency.

    With no last_refresh_time, every outflow counts as not yet reflected (conservative).
    """
    zero = Money.zero(balance.available.currency)
    after = sum(
        (
            outflow.amount
            for outflow in outflows
            if balance.last_refresh_time is None or outflow.submitted_at > balance.last_refresh_time
        ),
        zero,
    )
    reservations = list(reservations)
    cash_reserved = sum((r.amount for r in reservations if r.kind in CASH_KINDS), zero)
    earmarked = sum((r.amount for r in reservations if r.kind == EARMARK), zero)
    effective = balance.available - after
    return CashPosition(
        available=balance.available,
        outflows_after_refresh=after,
        effective_available=effective,
        cash_reserved=cash_reserved,
        earmarked=earmarked,
        unreserved=effective - cash_reserved,
    )
