"""WRITE_AHEAD, HANDOFF 7: "The operation record is committed before any PayPal call"."""

from countersign.fsm.operation import OperationState
from countersign.shield.engine import RuleName, RuleResult, blocked, passed
from countersign.shield.snapshot import OPERATION_KIND, Proposal, Snapshot

NAME = RuleName.WRITE_AHEAD


def check(proposal: Proposal, snap: Snapshot) -> RuleResult | None:
    """At execution, a PayPal write needs its own PENDING operation with the same payload hash.

    Before execution there is no operation yet, so the rule is not evaluated then.
    """
    kind = OPERATION_KIND.get(proposal.action)
    if kind is None or not snap.at_execution:
        return None
    op = snap.operation
    if op is None:
        return blocked(NAME, ["no operation recorded before the call"])
    reasons = []
    if op.state is not OperationState.PENDING:
        reasons.append(f"operation {op.id} is {op.state}, not PENDING")
    if op.payload_hash != proposal.payload_hash:
        reasons.append(f"operation {op.id} payload hash differs from the proposal's")
    if op.kind != kind:
        reasons.append(f"operation {op.id} is {op.kind}, not {kind}")
    if op.proposal_id != proposal.id:
        reasons.append(f"operation {op.id} belongs to another proposal")
    if reasons:
        return blocked(NAME, reasons)
    return passed(NAME, "operation recorded before call", operation_id=op.id)
