"""Payout line: one maker in one payout run, grouping that maker's obligations into one item.

PLAN section 2.3. A replacement is a new attempt on the same line, so UNPAID goes back to PAYING.
"""

from enum import StrEnum

from countersign.fsm import Machine


class LineState(StrEnum):
    APPROVED = "APPROVED"
    PAYING = "PAYING"
    PAID = "PAID"
    UNPAID = "UNPAID"
    RELEASED = "RELEASED"


class LineEvent(StrEnum):
    ATTEMPT_PREPARED = "ATTEMPT_PREPARED"
    ATTEMPT_PAID = "ATTEMPT_PAID"
    ATTEMPT_NOT_PAID = "ATTEMPT_NOT_PAID"
    REPLACEMENT_APPROVED = "REPLACEMENT_APPROVED"
    RELEASED = "RELEASED"


S, E = LineState, LineEvent

TABLE: dict[tuple[LineState, LineEvent], LineState] = {
    (S.APPROVED, E.ATTEMPT_PREPARED): S.PAYING,
    (S.PAYING, E.ATTEMPT_PAID): S.PAID,
    (S.PAYING, E.ATTEMPT_NOT_PAID): S.UNPAID,
    (S.UNPAID, E.REPLACEMENT_APPROVED): S.PAYING,
    (S.APPROVED, E.RELEASED): S.RELEASED,
    (S.UNPAID, E.RELEASED): S.RELEASED,
}

del S, E

LINE = Machine("line", LineState, LineEvent, TABLE)
