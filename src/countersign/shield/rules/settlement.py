"""SETTLEMENT, HANDOFF 7: "An obligation in SETTLED can never be paid again unless PayPal confirmed a return"."""

from countersign.fsm.attempt import LIVE
from countersign.fsm.obligation import ObligationState
from countersign.shield.engine import RuleName, RuleResult, blocked, money_text, passed
from countersign.shield.snapshot import PAYING, Proposal, Snapshot

NAME = RuleName.SETTLEMENT


def check(proposal: Proposal, snap: Snapshot) -> RuleResult | None:
    """Every obligation an item pays is unsettled and not already in a live attempt.

    A GET-confirmed clawback moves SETTLED back to OPEN (PAYOUT_CLAWED_BACK), so SETTLED here
    always means paid. The identical replay of an UNKNOWN attempt is the same payment, so its
    own IN_FLIGHT obligations and live attempt do not count against it.
    """
    if proposal.action not in PAYING:
        return None
    if not snap.payouts:
        return blocked(NAME, ["the proposal pays nothing the shield can check"])
    reasons: list[str] = []
    paid = 0
    for item in snap.payouts:
        line = snap.line_by_id.get(item.line_id)
        if line is None:
            reasons.append(f"line {item.line_id} not loaded")
            continue
        if item.amount != line.amount:
            reasons.append(
                f"item pays {money_text(item.amount)} but line {line.id} owes "
                f"{money_text(line.amount)}"
            )
        obligations = snap.line_obligations(line.id)
        if not obligations:
            reasons.append(f"line {line.id} has no obligations to pay")
        replay = snap.replay_of(item)
        for o in obligations:
            paid += 1
            if o.state is ObligationState.SETTLED:
                reasons.append(f"obligation {o.id} is already SETTLED")
            elif o.state is ObligationState.IN_FLIGHT and replay is None:
                reasons.append(f"obligation {o.id} is IN_FLIGHT in a live attempt")
        if replay is None:
            reasons += [
                f"line {line.id} attempt {a.attempt_no} is still live ({a.state})"
                for a in snap.line_attempts(line.id)
                if a.state in LIVE
            ]
    if reasons:
        return blocked(NAME, reasons)
    return passed(NAME, f"{paid} obligation(s) unsettled and in no live attempt")
