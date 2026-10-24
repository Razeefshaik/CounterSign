"""Use the mirrored seller response deadline and an explicit policy threshold."""
from datetime import datetime

from countersign.shield.engine import RuleName, blocked, needs_owner, passed
from countersign.shield.rules.facts import dispute
from countersign.shield.snapshot import NON_FINANCIAL, TIER, Action, Tier

NAME = RuleName.DEADLINE_GUARD


def check(proposal, snap):
    if proposal.action in NON_FINANCIAL:
        return None
    if not snap.targets.dispute_ids and proposal.action not in {Action.PROVIDE_EVIDENCE, Action.ACCEPT_CLAIM}:
        return None
    row = dispute(proposal, snap)
    if row is None:
        return blocked(NAME, ["dispute deadline target is unavailable"])
    due = row.seller_response_due_date
    if not isinstance(due, datetime) or due.utcoffset() is None:
        return blocked(NAME, ["seller_response_due_date is unavailable"])
    if snap.now >= due:
        return blocked(NAME, ["seller response deadline has passed"])
    close = snap.policies.get("deadline_close_seconds")
    if type(close) is not int or close <= 0:
        return needs_owner(NAME, ["deadline_close_seconds policy is missing or invalid"])
    if (due - snap.now).total_seconds() <= close and TIER[proposal.action] is Tier.OWNER:
        return needs_owner(NAME, ["seller response deadline is close; owner action is urgent"])
    return passed(NAME, "seller response deadline is open; action may proceed at its configured tier")
