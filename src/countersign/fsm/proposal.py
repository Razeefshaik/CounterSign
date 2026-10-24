"""Proposal: one structured action from the agent, from shield verdict to execution (PLAN 2.3).

State and verdict names match CONTRACTS section 1. Only an ALLOWED or owner-APPROVED proposal can
start executing; the shield re-runs every rule at execution (RECHECK_FAILED).
"""

from enum import StrEnum

from countersign.fsm import Machine


class ProposalState(StrEnum):
    CREATED = "CREATED"
    ALLOWED = "ALLOWED"
    BLOCKED = "BLOCKED"
    NEEDS_OWNER = "NEEDS_OWNER"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    EXECUTING = "EXECUTING"
    EXECUTED = "EXECUTED"
    BLOCKED_AT_EXECUTION = "BLOCKED_AT_EXECUTION"
    FAILED = "FAILED"


class ProposalEvent(StrEnum):
    VERDICT_ALLOW = "VERDICT_ALLOW"
    VERDICT_BLOCK = "VERDICT_BLOCK"
    VERDICT_NEEDS_OWNER = "VERDICT_NEEDS_OWNER"
    OWNER_APPROVED = "OWNER_APPROVED"
    OWNER_REJECTED = "OWNER_REJECTED"
    APPROVAL_EXPIRED = "APPROVAL_EXPIRED"
    EXECUTION_STARTED = "EXECUTION_STARTED"
    RECHECK_FAILED = "RECHECK_FAILED"  # shield re-run at execution did not allow it
    EXECUTION_SUCCEEDED = "EXECUTION_SUCCEEDED"
    EXECUTION_FAILED = "EXECUTION_FAILED"


S, E = ProposalState, ProposalEvent

# Shield verdict (proposals.verdict, CONTRACTS "Verdicts") -> the event it fires on a CREATED proposal.
VERDICT_EVENT: dict[str, ProposalEvent] = {
    "ALLOW": E.VERDICT_ALLOW,
    "BLOCK": E.VERDICT_BLOCK,
    "NEEDS_OWNER": E.VERDICT_NEEDS_OWNER,
}

TABLE: dict[tuple[ProposalState, ProposalEvent], ProposalState] = {
    (S.CREATED, E.VERDICT_ALLOW): S.ALLOWED,
    (S.CREATED, E.VERDICT_BLOCK): S.BLOCKED,
    (S.CREATED, E.VERDICT_NEEDS_OWNER): S.NEEDS_OWNER,
    (S.NEEDS_OWNER, E.OWNER_APPROVED): S.APPROVED,
    (S.NEEDS_OWNER, E.OWNER_REJECTED): S.REJECTED,
    (S.NEEDS_OWNER, E.APPROVAL_EXPIRED): S.EXPIRED,
    (S.ALLOWED, E.EXECUTION_STARTED): S.EXECUTING,
    (S.APPROVED, E.EXECUTION_STARTED): S.EXECUTING,
    (S.EXECUTING, E.EXECUTION_SUCCEEDED): S.EXECUTED,
    (S.EXECUTING, E.RECHECK_FAILED): S.BLOCKED_AT_EXECUTION,
    (S.EXECUTING, E.EXECUTION_FAILED): S.FAILED,
}

PROPOSAL = Machine("proposal", ProposalState, ProposalEvent, TABLE)
