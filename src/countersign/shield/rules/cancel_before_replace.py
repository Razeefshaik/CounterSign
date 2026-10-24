"""CANCEL_BEFORE_REPLACE, HANDOFF 7 with PLAN F2: "Replacement only after PayPal confirms the cancel: a GET shows the item RETURNED ... not a webhook alone"."""

from countersign.fsm.attempt import AttemptState
from countersign.shield.engine import RuleName, RuleResult, blocked, passed
from countersign.shield.snapshot import PAYING, Proposal, Snapshot

NAME = RuleName.CANCEL_BEFORE_REPLACE

# GET-confirmed: PayPal holds no money for these items. CANCELED is a webhook name, not a status.
PAYPAL_NOT_PAID = frozenset({AttemptState.RETURNED, AttemptState.FAILED})
# Ours: a definite 4xx, so PayPal never created the item (no GET exists to confirm).
OURS_NOT_PAID = frozenset({AttemptState.REJECTED})


def check(proposal: Proposal, snap: Snapshot) -> RuleResult | None:
    """A replacement (attempt 2 or later) needs the previous attempt GET-confirmed not paid."""
    if proposal.action not in PAYING:
        return None
    reasons: list[str] = []
    confirmed: list[str] = []
    for item in snap.payouts:
        if item.attempt_no <= 1 or snap.replay_of(item) is not None:
            continue
        previous = next(
            (a for a in snap.line_attempts(item.line_id) if a.attempt_no == item.attempt_no - 1),
            None,
        )
        if previous is None:
            reasons.append(f"line {item.line_id} attempt {item.attempt_no - 1} not loaded")
        elif previous.state in OURS_NOT_PAID:
            confirmed.append(f"attempt {previous.attempt_no} REJECTED")
        elif previous.state not in PAYPAL_NOT_PAID:
            reasons.append(
                f"line {item.line_id} attempt {previous.attempt_no} is {previous.state}; "
                "replace only after a GET shows RETURNED (or FAILED)"
            )
        elif previous.last_checked_at is None:
            reasons.append(
                f"line {item.line_id} attempt {previous.attempt_no} {previous.state} "
                "was never confirmed by a GET"
            )
        else:
            confirmed.append(
                f"attempt {previous.attempt_no} {previous.state} "
                f"(GET {previous.last_checked_at.isoformat()})"
            )
    if reasons:
        return blocked(NAME, reasons)
    if not confirmed:
        return passed(NAME, "no replacement attempt")
    return passed(NAME, "previous " + ", ".join(confirmed))
