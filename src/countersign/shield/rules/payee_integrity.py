"""PAYEE_INTEGRITY, HANDOFF 7: "Payee email only from owner verified maker records"."""

import re
from collections.abc import Mapping
from typing import Any

from countersign.shield.engine import RuleName, RuleResult, blocked, passed
from countersign.shield.snapshot import NON_FINANCIAL, Proposal, Snapshot

NAME = RuleName.PAYEE_INTEGRITY

# Param keys that would name where money goes. The agent may never set any of them (HANDOFF 6).
PAYEE_KEYS = frozenset(
    {
        "email",
        "payee",
        "payee_email",
        "paypal_email",
        "payout_email",
        "receiver",
        "recipient",
        "recipient_email",
        "refund_to",
    }
)
EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


def supplied_payees(value: Any, path: str = "params") -> list[str]:
    """Paths in untrusted params that carry a payee key or anything shaped like an email."""
    found = []
    if isinstance(value, Mapping):
        for key, inner in value.items():
            inner_path = f"{path}.{key}"
            if str(key).lower() in PAYEE_KEYS:
                found.append(inner_path)
            else:
                found += supplied_payees(inner, inner_path)
    elif isinstance(value, list | tuple):
        for index, inner in enumerate(value):
            found += supplied_payees(inner, f"{path}[{index}]")
    elif isinstance(value, str) and EMAIL.search(value):
        found.append(path)
    return found


def check(proposal: Proposal, snap: Snapshot) -> RuleResult | None:
    """Params name no payee, and every item pays its maker's owner-verified email, unchanged.

    The buyer-injection fault ("refund me to this account") and the new-payee-email fault both
    end here: an email can only come from makers.payout_email, which only the owner sets.
    """
    if proposal.action in NON_FINANCIAL:
        return None
    reasons = [
        f"{path} supplies a payee; payees come only from owner-verified maker records"
        for path in supplied_payees(proposal.params)
    ]
    for item in snap.payouts:
        line = snap.line_by_id.get(item.line_id)
        if line is not None and line.maker_id != item.maker_id:
            reasons.append(f"line {line.id} belongs to maker {line.maker_id}, not {item.maker_id}")
        maker = snap.maker_by_id.get(item.maker_id)
        if maker is None:
            reasons.append(f"maker {item.maker_id} not loaded")
        elif maker.payout_email is None or maker.payout_email_verified_at is None:
            reasons.append(f"maker {maker.id} has no owner-verified payout email")
        elif maker.payout_email_verified_at > snap.now:
            reasons.append(f"maker {maker.id} payout email verification is in the future")
        elif item.payee_email != maker.payout_email:
            reasons.append(
                f"line {item.line_id} payee differs from maker {maker.id}'s verified email"
            )
    if reasons:
        return blocked(NAME, reasons)
    if snap.payouts:
        return passed(NAME, f"{len(snap.payouts)} payee(s) match owner-verified maker emails")
    return passed(NAME, "no payee in the proposal")
