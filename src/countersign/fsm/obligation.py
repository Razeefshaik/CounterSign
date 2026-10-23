"""Obligation: what the shop owes one maker for one order share (or an adjustment).

PLAN section 2.3. Two safety rules live in what the table leaves out:
- IN_FLIGHT + DISPUTE_OPENED has no transition. The money may already have left, so the caller
  opens a PAID_BEFORE_DISPUTE incident for the owner instead of reserving it.
- UNCLAIMED keeps IN_FLIGHT. Only ATTEMPT_PAID (the item GET-confirmed SUCCESS) reaches SETTLED,
  so SETTLED only ever means "PayPal says SUCCESS".
"""

from enum import StrEnum

from countersign.fsm import Machine


class ObligationState(StrEnum):
    OPEN = "OPEN"
    RESERVED = "RESERVED"
    SCHEDULED = "SCHEDULED"
    IN_FLIGHT = "IN_FLIGHT"
    SETTLED = "SETTLED"
    VOID = "VOID"


class ObligationEvent(StrEnum):
    DISPUTE_OPENED = "DISPUTE_OPENED"
    LINE_APPROVED = "LINE_APPROVED"
    ORDER_REFUNDED = "ORDER_REFUNDED"
    DISPUTE_WON = "DISPUTE_WON"
    DISPUTE_CANCELLED = "DISPUTE_CANCELLED"
    DISPUTE_LOST_PARTIAL = "DISPUTE_LOST_PARTIAL"
    DISPUTE_LOST_FULL = "DISPUTE_LOST_FULL"
    LINE_RELEASED = "LINE_RELEASED"
    ATTEMPT_PREPARED = "ATTEMPT_PREPARED"
    ATTEMPT_PAID = "ATTEMPT_PAID"
    ATTEMPT_NOT_PAID = "ATTEMPT_NOT_PAID"
    PAYOUT_CLAWED_BACK = "PAYOUT_CLAWED_BACK"


S, E = ObligationState, ObligationEvent

TABLE: dict[tuple[ObligationState, ObligationEvent], ObligationState] = {
    (S.OPEN, E.DISPUTE_OPENED): S.RESERVED,
    (S.OPEN, E.LINE_APPROVED): S.SCHEDULED,
    (S.OPEN, E.ORDER_REFUNDED): S.VOID,
    (S.RESERVED, E.DISPUTE_WON): S.OPEN,
    (S.RESERVED, E.DISPUTE_CANCELLED): S.OPEN,
    # Same transaction: an ADJUSTMENT obligation for the refunded share (F16).
    (S.RESERVED, E.DISPUTE_LOST_PARTIAL): S.OPEN,
    (S.RESERVED, E.DISPUTE_LOST_FULL): S.VOID,
    # The line amount changes, so the run approval is voided by the version check.
    (S.SCHEDULED, E.DISPUTE_OPENED): S.RESERVED,
    # The owner defers, or the run is cancelled before submit.
    (S.SCHEDULED, E.LINE_RELEASED): S.OPEN,
    (S.SCHEDULED, E.ATTEMPT_PREPARED): S.IN_FLIGHT,
    # Attempt GET-confirmed SUCCESS.
    (S.IN_FLIGHT, E.ATTEMPT_PAID): S.SETTLED,
    # GET-confirmed FAILED / RETURNED / REJECTED; the line goes to UNPAID.
    (S.IN_FLIGHT, E.ATTEMPT_NOT_PAID): S.SCHEDULED,
    # GET-confirmed REFUNDED / REVERSED on the settling item.
    (S.SETTLED, E.PAYOUT_CLAWED_BACK): S.OPEN,
}

del S, E

OBLIGATION = Machine("obligation", ObligationState, ObligationEvent, TABLE)
