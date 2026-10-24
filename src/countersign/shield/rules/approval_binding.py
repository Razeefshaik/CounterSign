"""APPROVAL_BINDING, HANDOFF 7: "Approval covers the exact operation payload, record versions, expiry and single use; anything changed voids it"."""

from countersign.shield.engine import RuleName, RuleResult, blocked, needs_owner, passed
from countersign.shield.snapshot import TIER, Proposal, Snapshot, Tier

NAME = RuleName.APPROVAL_BINDING


def check(proposal: Proposal, snap: Snapshot) -> RuleResult | None:
    """Before execution, an owner-tier action asks for approval of these exact record versions.
    At execution, the approval must be this proposal's, unused, unexpired, for the same payload
    hash, and over versions equal to the current ones (Snapshot.record_versions)."""
    owner_tier = TIER[proposal.action] is Tier.OWNER
    current = snap.record_versions()
    if not snap.at_execution:
        if not owner_tier:
            return None
        return needs_owner(
            NAME,
            [f"{proposal.action} is owner tier; the owner approves this exact payload"],
            payload_hash=proposal.payload_hash,
            record_versions=current,
        )
    approval = snap.approval
    if approval is None:
        if not owner_tier:
            return None
        return blocked(NAME, [f"{proposal.action} has no owner approval"])
    reasons = []
    if approval.proposal_id != proposal.id:
        reasons.append(f"approval {approval.id} is for another proposal")
    if approval.used_at is not None:
        reasons.append(f"approval {approval.id} was already used at {approval.used_at.isoformat()}")
    if snap.now >= approval.expires_at:
        reasons.append(f"approval {approval.id} expired at {approval.expires_at.isoformat()}")
    if approval.payload_hash != proposal.payload_hash:
        reasons.append("payload changed since approval")
    bound = dict(approval.record_versions)
    for key in sorted(bound.keys() | current.keys()):
        if bound.get(key) != current.get(key):
            was, now = bound.get(key, "absent"), current.get(key, "absent")
            reasons.append(f"{key} version {was} at approval, {now} now")
    if reasons:
        return blocked(NAME, ["stale approval: " + reasons[0], *reasons[1:]])
    return passed(
        NAME,
        f"approval {approval.id} bound: same payload, {len(current)} record version(s), "
        f"unused, expires {approval.expires_at.isoformat()}",
        approval_id=approval.id,
    )
