"""PAYOUT_STATE_MACHINE, HANDOFF 7: "No new attempt while the previous one is PENDING, ONHOLD/HELD, BLOCKED or UNKNOWN; cancel only when UNCLAIMED"."""

from enum import StrEnum

from countersign.fsm import IllegalTransition, Machine
from countersign.fsm.attempt import ATTEMPT, LIVE, AttemptEvent, AttemptState
from countersign.fsm.line import LINE, LineEvent, LineState
from countersign.fsm.obligation import OBLIGATION, ObligationEvent, ObligationState
from countersign.shield.engine import RuleName, RuleResult, blocked, passed
from countersign.shield.snapshot import PAYING, Action, PayoutItem, Proposal, Snapshot

NAME = RuleName.PAYOUT_STATE_MACHINE


def _legal(machine: Machine, state: StrEnum, event: StrEnum) -> bool:
    try:
        machine.next(state, event)
    except IllegalTransition:
        return False
    return True


def _payout(item: PayoutItem, snap: Snapshot) -> list[str]:
    line = snap.line_by_id.get(item.line_id)
    if line is None:
        return [f"line {item.line_id} not loaded"]
    obligations = snap.line_obligations(line.id)
    replay = snap.replay_of(item)
    if replay is not None:
        # Resolving UNKNOWN: the attempt, line and obligations stay where they are.
        reasons: list[str] = []
        if not _legal(ATTEMPT, replay.state, AttemptEvent.REPLAY_CONFIRMED):
            reasons.append(f"attempt {replay.id} cannot be replayed from {replay.state}")
        if line.state is not LineState.PAYING:
            reasons.append(f"line {line.id} is {line.state}, not PAYING, under an UNKNOWN attempt")
        reasons += [
            f"obligation {o.id} is {o.state}, not IN_FLIGHT, under an UNKNOWN attempt"
            for o in obligations
            if o.state is not ObligationState.IN_FLIGHT
        ]
        return reasons
    reasons = []
    attempts = snap.line_attempts(line.id)
    reasons += [
        f"no new attempt while attempt {a.attempt_no} on line {line.id} is {a.state}"
        for a in attempts
        if a.state in LIVE
    ]
    expected_no = attempts[-1].attempt_no + 1 if attempts else 1
    if item.attempt_no != expected_no:
        reasons.append(f"line {line.id} next attempt is {expected_no}, not {item.attempt_no}")
    if item.attempt_no == 1:
        if not _legal(LINE, line.state, LineEvent.ATTEMPT_PREPARED):
            reasons.append(f"line {line.id} is {line.state}; a first attempt needs APPROVED")
    elif not (
        _legal(LINE, line.state, LineEvent.REPLACEMENT_APPROVED)
        or (snap.at_execution and line.state is LineState.PAYING)
    ):
        reasons.append(f"line {line.id} is {line.state}; a replacement needs UNPAID")
    reasons += [
        f"obligation {o.id} is {o.state}; an attempt needs SCHEDULED"
        for o in obligations
        if not _legal(OBLIGATION, o.state, ObligationEvent.ATTEMPT_PREPARED)
    ]
    return reasons


def _cancel(snap: Snapshot) -> list[str]:
    if not snap.targets.attempt_ids:
        return ["no payout attempt named to cancel"]
    reasons = []
    for attempt_id in snap.targets.attempt_ids:
        attempt = snap.attempt_by_id.get(attempt_id)
        if attempt is None:
            reasons.append(f"attempt {attempt_id} not loaded")
        elif attempt.state is not AttemptState.UNCLAIMED:
            reasons.append(f"attempt {attempt_id} is {attempt.state}; cancel only when UNCLAIMED")
    return reasons


def _defer(snap: Snapshot) -> list[str]:
    if not snap.targets.line_ids:
        return ["no payout line named to defer"]
    reasons = []
    for line_id in snap.targets.line_ids:
        line = snap.line_by_id.get(line_id)
        if line is None:
            reasons.append(f"line {line_id} not loaded")
            continue
        if not _legal(LINE, line.state, LineEvent.RELEASED):
            reasons.append(f"line {line_id} is {line.state}; only a payout not yet sent defers")
        reasons += [
            f"obligation {o.id} is {o.state}; it cannot be released"
            for o in snap.line_obligations(line_id)
            if not _legal(OBLIGATION, o.state, ObligationEvent.LINE_RELEASED)
        ]
    return reasons


def check(proposal: Proposal, snap: Snapshot) -> RuleResult | None:
    """The proposed move is legal in countersign.fsm for every attempt, line and obligation."""
    if proposal.action in PAYING:
        if not snap.payouts:
            return blocked(NAME, ["the proposal pays nothing the shield can check"])
        reasons = [r for item in snap.payouts for r in _payout(item, snap)]
        ok = f"{len(snap.payouts)} attempt(s) legal; no live attempt on their lines"
    elif proposal.action is Action.CANCEL_UNCLAIMED_PAYOUT:
        reasons = _cancel(snap)
        ok = "item UNCLAIMED; cancel permitted"
    elif proposal.action is Action.DEFER_SCHEDULED_PAYOUT:
        reasons = _defer(snap)
        ok = "payout not yet sent; release permitted"
    else:
        return None
    return blocked(NAME, reasons) if reasons else passed(NAME, ok)
