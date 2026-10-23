"""UNKNOWN_SCOPED, HANDOFF 7: "Unknown state blocks only financial actions on the affected obligation or dispute, not the whole system"."""

from countersign.fsm.attempt import AttemptState
from countersign.fsm.dispute import Phase
from countersign.fsm.operation import OperationState
from countersign.shield.engine import RuleName, RuleResult, blocked, passed
from countersign.shield.snapshot import NON_FINANCIAL, Proposal, Snapshot

NAME = RuleName.UNKNOWN_SCOPED


def check(proposal: Proposal, snap: Snapshot) -> RuleResult | None:
    """Block when anything the proposal touches is unknown; ignore unknowns elsewhere.

    Unknown means an UNKNOWN attempt or operation, a dispute in UNKNOWN_OUTCOME, or a touched
    record that was not loaded. The identical replay of an UNKNOWN attempt is how that attempt
    gets resolved (F1), so it is not blocked by its own UNKNOWN state.
    """
    if proposal.action in NON_FINANCIAL:
        return None
    scope = snap.scope
    replayed = [a for item in snap.payouts if (a := snap.replay_of(item)) is not None]
    replayed_ids = {a.id for a in replayed}
    replayed_ops = {a.operation_id for a in replayed}
    touched = scope.lines | scope.obligations | scope.attempts | scope.disputes
    reasons = [f"{key} not loaded; its state is unknown" for key in snap.missing()]
    unknown_attempts = [a for a in snap.attempts if a.state is AttemptState.UNKNOWN]
    reasons += [
        f"attempt {a.id} on line {a.line_id} is UNKNOWN"
        for a in unknown_attempts
        if a.id in scope.attempts and a.id not in replayed_ids
    ]
    ops = {op.id: op for op in snap.operations}
    if snap.operation is not None:
        ops[snap.operation.id] = snap.operation
    scope_ops = {
        snap.attempt_by_id[a].operation_id for a in scope.attempts if a in snap.attempt_by_id
    }
    unknown_ops = [op for op in ops.values() if op.state is OperationState.UNKNOWN]
    reasons += [
        f"operation {op.id} is UNKNOWN"
        for op in unknown_ops
        if (op.id in scope_ops or op.target_id in touched) and op.id not in replayed_ops
    ]
    reasons += [
        f"dispute {d.id} outcome is UNKNOWN_OUTCOME"
        for d in snap.disputes
        if d.id in scope.disputes and d.phase is Phase.UNKNOWN_OUTCOME
    ]
    if reasons:
        return blocked(NAME, reasons)
    elsewhere = sum(a.id not in scope.attempts for a in unknown_attempts) + sum(
        op.id not in scope_ops and op.target_id not in touched for op in unknown_ops
    )
    detail = f"no unknown state on the {len(touched)} touched record(s)"
    if elsewhere > 0:
        detail += f"; {elsewhere} unknown elsewhere, out of scope"
    return passed(NAME, detail)
