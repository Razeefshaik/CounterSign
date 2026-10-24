"""Refunds always need the owner and cannot exceed dispute or aggregate policy."""
from countersign.shield.engine import RuleName, blocked, needs_owner
from countersign.shield.rules.facts import dispute, has_link, policy_money
from countersign.shield.snapshot import Action

NAME = RuleName.REFUND_POLICY


def check(proposal, snap):
    if proposal.action is not Action.ACCEPT_CLAIM:
        return None
    row = dispute(proposal, snap)
    if row is None or row.amount is None or row.order_id is None:
        return blocked(NAME, ["refund dispute amount or linked order is unavailable"])
    if not has_link(row, "accept_claim") or row.status == "RESOLVED":
        return blocked(NAME, ["dispute has no available accept_claim link"])
    currency = row.amount.currency
    amount = row.amount.minor
    for key in ("amount", "refund_amount"):
        if key in proposal.params:
            requested = policy_money(proposal.params[key], currency)
            if requested is None or requested <= 0:
                return blocked(NAME, ["refund amount must be positive integer minor units in dispute currency"])
            if requested > row.amount.minor:
                return blocked(NAME, ["refund amount exceeds the dispute amount"])
    # ACCEPT_CLAIM refunds the full claim: smaller untrusted amounts cannot lower its budget.
    if amount <= 0:
        return blocked(NAME, ["refund amount exceeds the dispute amount or is invalid"])
    limits = [policy_money(snap.policies.get(key), currency)
              for key in ("refund_limit_per_order", "refund_limit_per_day")]
    if any(limit is None for limit in limits):
        return blocked(NAME, ["refund policy limits are missing or invalid for this currency"])
    orders, days = snap.refund_totals.get("orders"), snap.refund_totals.get("day")
    if not isinstance(orders, dict) or not isinstance(days, dict):
        return blocked(NAME, ["aggregate refund history is unavailable"])
    used = [orders.get(f"{row.order_id}:{currency}", 0), days.get(currency, 0)]
    if any(type(value) is not int or value < 0 for value in used):
        return blocked(NAME, ["aggregate refund history is invalid"])
    if any(amount + total > limit for total, limit in zip(used, limits, strict=True)):
        return blocked(NAME, ["refund exceeds per-order or per-day policy limit"])
    return needs_owner(NAME, ["ACCEPT_CLAIM and every refund require the owner at any amount"])
