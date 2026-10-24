"""FUNDING, HANDOFF 7 with PLAN 2.3 (F12): "Unreserved available cash must cover the operation; pending proposals and approved runs reserve their cash"."""

from countersign.ledger.cash import CASH_KINDS
from countersign.money import Money
from countersign.shield.engine import RuleName, RuleResult, blocked, money_text, passed
from countersign.shield.snapshot import PAYING, Proposal, Snapshot

NAME = RuleName.FUNDING


def check(proposal: Proposal, snap: Snapshot) -> RuleResult | None:
    """The payout total fits in unreserved cash plus the cash this proposal (or its run) already
    reserved, which unreserved has subtracted once and must not be counted twice.

    Dispute earmarks never reduce unreserved (cash_position). An identical replay of an UNKNOWN
    attempt sends no new money, so it needs none.
    """
    if proposal.action not in PAYING:
        return None
    currency = snap.cash.unreserved.currency
    reasons = []
    for item in snap.payouts:
        if item.amount.currency != currency:
            reasons.append(f"line {item.line_id} pays {item.amount.currency}, cash is {currency}")
        elif item.amount.minor <= 0:
            reasons.append(f"line {item.line_id} amount is not positive")
    if reasons:
        return blocked(NAME, reasons)
    zero = Money.zero(currency)
    need = sum((i.amount for i in snap.payouts if snap.replay_of(i) is None), zero)
    run_ids = {
        snap.line_by_id[i.line_id].run_id for i in snap.payouts if i.line_id in snap.line_by_id
    }
    own = sum(
        (
            r.amount
            for r in snap.reservations
            if r.state == "ACTIVE"
            and r.kind in CASH_KINDS
            and (r.proposal_id == proposal.id or (r.run_id is not None and r.run_id in run_ids))
        ),
        zero,
    )
    covering = snap.cash.unreserved + own
    facts = {"need": need.minor, "unreserved": snap.cash.unreserved.minor, "own": own.minor}
    if need > covering:
        return blocked(
            NAME,
            [
                (
                    f"needs {money_text(need)} but unreserved is "
                    f"{money_text(snap.cash.unreserved)} plus {money_text(own)} this proposal holds"
                )
            ],
            **facts,
        )
    return passed(
        NAME,
        f"{money_text(need)} covered by unreserved {money_text(snap.cash.unreserved)} "
        f"plus {money_text(own)} this proposal holds",
        **facts,
    )
