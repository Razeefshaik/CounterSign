"""Dispute: PayPal's status is mirrored verbatim; CounterSign acts on a derived phase (PLAN 2.3).

There is no transition table. PayPal owns the status and moves it (no document lists every move,
PLAN F8), so the status column has no enum or CHECK. phase() is a pure function of the mirrored
fields, recomputed on every sync; it never depends on the previous phase, so there is no move to
check. The obligation machine carries the moves that matter (DISPUTE_WON, DISPUTE_LOST_FULL, ...).
"""

from collections.abc import Mapping
from enum import StrEnum


class Phase(StrEnum):
    ACTIVE = "ACTIVE"
    WON = "WON"
    LOST_FULL = "LOST_FULL"
    LOST_PARTIAL = "LOST_PARTIAL"
    CANCELLED = "CANCELLED"
    UNKNOWN_OUTCOME = "UNKNOWN_OUTCOME"


RESOLVED_STATUS = "RESOLVED"

# PayPal outcome_code -> phase, filled ONLY from codes recorded in D10 [pending D10].
# A loss code may map to LOST_FULL or LOST_PARTIAL: the refunded amount decides which.
OUTCOME_MAP: dict[str, Phase] = {}

# UNKNOWN_OUTCOME fails closed: the reserve stays and an incident opens, scoped to this dispute.
KEEPS_RESERVE = frozenset({Phase.ACTIVE, Phase.UNKNOWN_OUTCOME})
OPENS_INCIDENT = frozenset({Phase.UNKNOWN_OUTCOME})

_LOSS = frozenset({Phase.LOST_FULL, Phase.LOST_PARTIAL})
_NO_REFUND = frozenset({Phase.WON, Phase.CANCELLED})


def phase(
    status: str | None,
    outcome_code: str | None,
    amount_refunded_minor: int | None,
    dispute_amount_minor: int | None,
    *,
    outcome_map: Mapping[str, Phase] | None = None,
) -> Phase:
    """Phase from the mirrored dispute; both amounts in minor units of the dispute's currency.

    Anything the map or the amounts cannot settle is UNKNOWN_OUTCOME, never a guess.
    """
    if status != RESOLVED_STATUS:
        return Phase.ACTIVE
    codes = OUTCOME_MAP if outcome_map is None else outcome_map
    mapped = codes.get(outcome_code) if outcome_code is not None else None
    if mapped in _LOSS:
        return _loss_size(amount_refunded_minor, dispute_amount_minor)
    if mapped in _NO_REFUND:
        return Phase(mapped) if amount_refunded_minor in (None, 0) else Phase.UNKNOWN_OUTCOME
    return Phase.UNKNOWN_OUTCOME


def _loss_size(refunded: int | None, amount: int | None) -> Phase:
    if refunded is None or amount is None or amount <= 0:
        return Phase.UNKNOWN_OUTCOME
    if refunded == amount:
        return Phase.LOST_FULL
    if 0 < refunded < amount:
        return Phase.LOST_PARTIAL
    return Phase.UNKNOWN_OUTCOME  # a loss with nothing refunded, or more than was disputed
