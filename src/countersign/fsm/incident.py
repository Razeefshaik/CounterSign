"""Incident: one money problem in the queue, from opening to reconciled (PLAN 2.3).

PLAN gives OPEN -> INVESTIGATING -> AWAITING_OWNER | EXECUTING | WAITING_EXTERNAL -> ... -> RESOLVED.
The "..." moves here are the minimal loop back to INVESTIGATING. RESOLVED is reached only by
RECONCILED: the reconciler found obligations, reservations and GET-confirmed PayPal states in
agreement. It is never entered while a write is executing or an owner decision is open.
"""

from enum import StrEnum

from countersign.fsm import Machine


class IncidentState(StrEnum):
    OPEN = "OPEN"
    INVESTIGATING = "INVESTIGATING"
    AWAITING_OWNER = "AWAITING_OWNER"
    EXECUTING = "EXECUTING"
    WAITING_EXTERNAL = "WAITING_EXTERNAL"
    RESOLVED = "RESOLVED"


class IncidentEvent(StrEnum):
    INVESTIGATION_STARTED = "INVESTIGATION_STARTED"
    OWNER_NEEDED = "OWNER_NEEDED"  # a proposal got NEEDS_OWNER
    EXECUTION_STARTED = "EXECUTION_STARTED"  # an ALLOWED or APPROVED proposal started executing
    WAIT_STARTED = "WAIT_STARTED"  # the next step belongs to PayPal, the buyer or the maker
    OWNER_REJECTED = "OWNER_REJECTED"
    APPROVAL_EXPIRED = "APPROVAL_EXPIRED"
    EXECUTION_SUCCEEDED = "EXECUTION_SUCCEEDED"
    RECHECK_FAILED = "RECHECK_FAILED"
    EXECUTION_FAILED = "EXECUTION_FAILED"
    EXTERNAL_UPDATE = "EXTERNAL_UPDATE"  # a GET-confirmed change that needs new reasoning
    RECONCILED = "RECONCILED"


S, E = IncidentState, IncidentEvent

TABLE: dict[tuple[IncidentState, IncidentEvent], IncidentState] = {
    (S.OPEN, E.INVESTIGATION_STARTED): S.INVESTIGATING,
    (S.INVESTIGATING, E.OWNER_NEEDED): S.AWAITING_OWNER,
    (S.INVESTIGATING, E.EXECUTION_STARTED): S.EXECUTING,
    (S.INVESTIGATING, E.WAIT_STARTED): S.WAITING_EXTERNAL,
    (S.INVESTIGATING, E.RECONCILED): S.RESOLVED,
    (S.AWAITING_OWNER, E.EXECUTION_STARTED): S.EXECUTING,
    (S.AWAITING_OWNER, E.OWNER_REJECTED): S.INVESTIGATING,
    (S.AWAITING_OWNER, E.APPROVAL_EXPIRED): S.INVESTIGATING,
    # A successful write still waits for the GET-confirmed state and the reconciler.
    (S.EXECUTING, E.EXECUTION_SUCCEEDED): S.WAITING_EXTERNAL,
    (S.EXECUTING, E.RECHECK_FAILED): S.INVESTIGATING,
    (S.EXECUTING, E.EXECUTION_FAILED): S.INVESTIGATING,
    (S.WAITING_EXTERNAL, E.EXTERNAL_UPDATE): S.INVESTIGATING,
    (S.WAITING_EXTERNAL, E.RECONCILED): S.RESOLVED,
}

INCIDENT = Machine("incident", IncidentState, IncidentEvent, TABLE)
