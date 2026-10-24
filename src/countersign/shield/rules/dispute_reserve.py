"""DISPUTE_RESERVE, HANDOFF 7: "A maker's share of an order under dispute is reserved, not paid, until the dispute ends"."""

from countersign.fsm.dispute import KEEPS_RESERVE
from countersign.fsm.obligation import ObligationState
from countersign.ledger.cash import EARMARK
from countersign.shield.engine import RuleName, RuleResult, blocked, passed
from countersign.shield.snapshot import PAYING, Action, Obligation, Proposal, Snapshot

NAME = RuleName.DISPUTE_RESERVE


def _earmarks(obligation_id: str, snap: Snapshot) -> list[str]:
    return [
        r.dispute_id or "?"
        for r in snap.reservations
        if r.kind == EARMARK and r.state == "ACTIVE" and r.obligation_id == obligation_id
    ]


def _unpayable(o: Obligation, snap: Snapshot) -> list[str]:
    reasons = []
    if o.state is ObligationState.RESERVED:
        reasons.append(f"obligation {o.id} is RESERVED for a dispute")
    if earmarked := _earmarks(o.id, snap):
        reasons.append(f"obligation {o.id} has an active dispute earmark ({', '.join(earmarked)})")
    reasons += [
        f"obligation {o.id} order is under dispute {d.id} ({d.phase})"
        for d in snap.order_disputes(o.order_id)
        if d.phase in KEEPS_RESERVE
    ]
    return reasons


def _release(snap: Snapshot) -> list[str]:
    if not snap.targets.obligation_ids:
        return ["no obligation named to release"]
    reasons = []
    for obligation_id in snap.targets.obligation_ids:
        o = snap.obligation_by_id.get(obligation_id)
        if o is None:
            reasons.append(f"obligation {obligation_id} not loaded")
            continue
        dispute_ids = {d.id for d in snap.order_disputes(o.order_id)} | set(_earmarks(o.id, snap))
        if not dispute_ids:
            reasons.append(f"obligation {o.id}: no dispute found to show it ended")
        for dispute_id in sorted(dispute_ids):
            dispute = snap.dispute_by_id.get(dispute_id)
            if dispute is None:
                reasons.append(f"dispute {dispute_id} not loaded")
            elif dispute.phase in KEEPS_RESERVE:
                reasons.append(f"dispute {dispute_id} is {dispute.phase}; the reserve stays")
    return reasons


def check(proposal: Proposal, snap: Snapshot) -> RuleResult | None:
    """Pay no obligation that is reserved, earmarked or on a disputed order; release only once
    every dispute on it has ended (UNKNOWN_OUTCOME keeps the reserve, fsm.dispute)."""
    if proposal.action in PAYING:
        reasons = [
            r
            for item in snap.payouts
            for o in snap.line_obligations(item.line_id)
            for r in _unpayable(o, snap)
        ]
        ok = "no paid obligation is reserved or on a disputed order"
    elif proposal.action is Action.RELEASE_RESERVE:
        reasons = _release(snap)
        ok = "every dispute behind the reserve has ended"
    elif proposal.action is Action.RESERVE_EARNINGS:
        reasons, ok = [], "reserving pays nobody"
    else:
        return None
    return blocked(NAME, reasons) if reasons else passed(NAME, ok)
