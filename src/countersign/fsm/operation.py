"""Operation: the write-ahead record of every intended PayPal write (PLAN 2.3).

The executor inserts the row as PENDING before any HTTP call. UNKNOWN (lost response) leaves only
through the reconciler's identical-key replay: RESOLVED_OK or RESOLVED_NOT_DONE, never a guess.
"""

from enum import StrEnum

from countersign.fsm import Machine


class OperationState(StrEnum):
    PENDING = "PENDING"
    IN_FLIGHT = "IN_FLIGHT"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"
    BLOCKED = "BLOCKED"


class OperationEvent(StrEnum):
    # Transaction 1: rows locked, then every rule and the approval binding re-run.
    LOCK_AND_RECHECK_OK = "LOCK_AND_RECHECK_OK"
    RECHECK_FAILED = "RECHECK_FAILED"
    OK = "OK"
    DEFINITE_ERROR = "DEFINITE_ERROR"
    AMBIGUOUS = "AMBIGUOUS"  # timeout, connection reset, 5xx
    RESOLVED_OK = "RESOLVED_OK"
    RESOLVED_NOT_DONE = "RESOLVED_NOT_DONE"


S, E = OperationState, OperationEvent

TABLE: dict[tuple[OperationState, OperationEvent], OperationState] = {
    (S.PENDING, E.LOCK_AND_RECHECK_OK): S.IN_FLIGHT,
    (S.PENDING, E.RECHECK_FAILED): S.BLOCKED,
    (S.IN_FLIGHT, E.OK): S.SUCCEEDED,
    (S.IN_FLIGHT, E.DEFINITE_ERROR): S.FAILED,
    (S.IN_FLIGHT, E.AMBIGUOUS): S.UNKNOWN,
    (S.UNKNOWN, E.RESOLVED_OK): S.SUCCEEDED,
    (S.UNKNOWN, E.RESOLVED_NOT_DONE): S.FAILED,
}

OPERATION = Machine("operation", OperationState, OperationEvent, TABLE)
