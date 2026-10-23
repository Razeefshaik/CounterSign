"""Payout attempt: one PayPal payout item for a line.

PLAN section 2.3. The first four states are ours and move through the ATTEMPT machine. From
SUBMITTED onward the state mirrors the item transaction_status that PayPal's GET returns, via
mirror(). A webhook never moves state: it only triggers a GET, and the GET result is mirrored.
"""

from datetime import timedelta
from enum import StrEnum

from countersign.fsm import IllegalTransition, Machine


class AttemptState(StrEnum):
    # Ours.
    PREPARED = "PREPARED"
    SUBMITTED = "SUBMITTED"
    UNKNOWN = "UNKNOWN"
    REJECTED = "REJECTED"
    # PayPal, non-terminal.
    PENDING = "PENDING"
    UNCLAIMED = "UNCLAIMED"
    ONHOLD = "ONHOLD"
    BLOCKED = "BLOCKED"
    # PayPal item transaction_status.
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    RETURNED = "RETURNED"
    REFUNDED = "REFUNDED"
    REVERSED = "REVERSED"


class AttemptEvent(StrEnum):
    HTTP_2XX = "HTTP_2XX"
    HTTP_DEFINITE_4XX = "HTTP_DEFINITE_4XX"
    AMBIGUOUS = "AMBIGUOUS"
    REPLAY_CONFIRMED = "REPLAY_CONFIRMED"


S, E = AttemptState, AttemptEvent

# States that hold the line: the DB index uq_one_live_attempt allows one per line.
LIVE = frozenset({S.PREPARED, S.SUBMITTED, S.UNKNOWN, S.PENDING, S.UNCLAIMED, S.ONHOLD, S.BLOCKED})

# Every documented item transaction_status (F2): the states mirror() may set.
PAYPAL_STATUSES = frozenset(
    {S.PENDING, S.UNCLAIMED, S.ONHOLD, S.BLOCKED}
    | {S.SUCCESS, S.FAILED, S.RETURNED, S.REFUNDED, S.REVERSED}
)

OURS: dict[tuple[AttemptState, AttemptEvent], AttemptState] = {
    # Batch id known; the item id comes from GET batch (F1).
    (S.PREPARED, E.HTTP_2XX): S.SUBMITTED,
    # Class list fixed from D6/D7 recordings. [pending D6, D7]
    (S.PREPARED, E.HTTP_DEFINITE_4XX): S.REJECTED,
    # Timeout, connection reset, 5xx.
    (S.PREPARED, E.AMBIGUOUS): S.UNKNOWN,
    # Identical keys+body replay, or a duplicate error whose HATEOAS link GETs to a batch
    # matching payee+amount. [pending D6]
    (S.UNKNOWN, E.REPLAY_CONFIRMED): S.SUBMITTED,
}

ATTEMPT = Machine("attempt", AttemptState, AttemptEvent, OURS)

# Expected mirror moves. Any other move is still recorded, and opens an incident.
PAYPAL_EXPECTED: dict[AttemptState, frozenset[AttemptState]] = {  # [pending D3-D5]
    S.PENDING: frozenset({S.SUCCESS, S.FAILED, S.UNCLAIMED, S.ONHOLD, S.BLOCKED}),
    S.ONHOLD: frozenset({S.PENDING, S.SUCCESS, S.FAILED, S.BLOCKED, S.RETURNED}),
    S.UNCLAIMED: frozenset({S.SUCCESS, S.RETURNED}),
    S.SUCCESS: frozenset({S.REFUNDED, S.REVERSED}),
}

# The first GET after SUBMITTED has no earlier PayPal status to compare with: a slow first GET
# can find any status a new item reaches on its own. RETURNED (30 days or a cancel), REFUNDED
# and REVERSED (web uploads only, F3) need history we never saw, so they still open an incident.
FIRST_MIRROR_EXPECTED = PAYPAL_STATUSES - {S.RETURNED, S.REFUNDED, S.REVERSED}

# PAYMENT.PAYOUTS-ITEM.<suffix> webhooks: the status to GET-confirm. None = GET decides.
# CANCELED and DENIED are not item statuses (F2). [pending D5, D7]
WEBHOOK_TO_STATUS: dict[str, AttemptState | None] = {
    "SUCCEEDED": S.SUCCESS,
    "HELD": S.ONHOLD,
    "UNCLAIMED": S.UNCLAIMED,
    "FAILED": S.FAILED,
    "BLOCKED": S.BLOCKED,
    "RETURNED": S.RETURNED,
    "REFUNDED": S.REFUNDED,
    "CANCELED": None,
    "DENIED": None,
}

# Under the documented 30-day key window. After it, UNKNOWN goes to the owner.
UNKNOWN_REPLAY_WINDOW = timedelta(days=25)

del S, E


def mirror(
    current: AttemptState | str, paypal_status: AttemptState | str
) -> tuple[AttemptState, bool]:
    """Mirror a GET item transaction_status onto an attempt that is SUBMITTED or later.

    Returns (new_state, expected). The new state is always paypal_status: a surprising status
    is never dropped. expected is False when the move is not in PAYPAL_EXPECTED, and the caller
    then opens an incident. The same status seen again is not a move, so it is expected.
    Webhooks never call this directly; they trigger the GET whose result is mirrored here.
    """
    try:
        current = AttemptState(current)
    except ValueError:
        raise IllegalTransition("attempt: unknown state.") from None
    if current is not AttemptState.SUBMITTED and current not in PAYPAL_STATUSES:
        raise IllegalTransition(f"attempt: {current} does not mirror PayPal yet.")
    try:
        new = AttemptState(paypal_status)
    except ValueError:
        new = None
    if new not in PAYPAL_STATUSES:
        raise ValueError(f"attempt: {paypal_status!r} is not a PayPal item transaction_status.")
    if current is AttemptState.SUBMITTED:
        return new, new in FIRST_MIRROR_EXPECTED
    return new, new is current or new in PAYPAL_EXPECTED.get(current, frozenset())
