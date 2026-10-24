"""IDEMPOTENCY, HANDOFF 7 with PLAN F1/F15: "sender_batch_id and PayPal-Request-Id derived from payout line id (UUIDv7) and attempt number; retries reuse them"."""

from dataclasses import dataclass
from uuid import UUID

from countersign.fsm.attempt import UNKNOWN_REPLAY_WINDOW
from countersign.shield.engine import RuleName, RuleResult, blocked, needs_owner, passed
from countersign.shield.snapshot import PAYING, PayoutItem, Proposal, Snapshot

NAME = RuleName.IDEMPOTENCY


@dataclass(frozen=True)
class Keys:
    sender_batch_id: str
    request_id: str
    sender_item_id: str


def derive_keys(line_id: str, attempt_no: int) -> Keys:
    """The keys for one attempt (CONTRACTS 5.2 shape): cs-<line_id>-a<n>, item cs-...-a<n>-i1."""
    base = f"cs-{line_id}-a{attempt_no}"
    return Keys(sender_batch_id=base, request_id=base, sender_item_id=f"{base}-i1")


def _is_uuid7(value: str) -> bool:
    try:
        return UUID(value).version == 7
    except ValueError:
        return False


def _keys(item: PayoutItem) -> tuple[str, str, str]:
    return item.sender_batch_id, item.request_id, item.sender_item_id


def check(proposal: Proposal, snap: Snapshot) -> RuleResult | None:
    """Keys are derived, unique to one payload, and an UNKNOWN attempt is only ever replayed.

    The replay must be identical and inside UNKNOWN_REPLAY_WINDOW (25 days, under PayPal's
    30-day key memory); after that a replay could create a second payout, so the owner decides.
    """
    if proposal.action not in PAYING:
        return None
    reasons: list[str] = []
    owner: list[str] = []
    replays = 0
    seen: set[str] = set()
    for item in snap.payouts:
        if not _is_uuid7(item.line_id):
            reasons.append(f"line id {item.line_id} is not a UUIDv7")
        if item.attempt_no < 1:
            reasons.append(f"attempt number {item.attempt_no} is not positive")
        expected = derive_keys(item.line_id, item.attempt_no)
        if _keys(item) != (expected.sender_batch_id, expected.request_id, expected.sender_item_id):
            reasons.append(f"line {item.line_id} attempt {item.attempt_no} keys are not derived")
        for key in {item.sender_batch_id, item.request_id}:
            if key in seen:
                reasons.append(f"key {key} appears twice in one proposal")
            seen.add(key)
        same_slot = [a for a in snap.line_attempts(item.line_id) if a.attempt_no == item.attempt_no]
        replay = snap.replay_of(item)
        if same_slot and replay is None:
            reasons += [
                f"line {item.line_id} attempt {a.attempt_no} already exists ({a.state}); "
                "its keys are never reused for another payload"
                for a in same_slot
            ]
        elif replay is not None:
            replays += 1
            age = snap.now - replay.prepared_at
            if age > UNKNOWN_REPLAY_WINDOW:
                owner.append(
                    f"UNKNOWN attempt {replay.id} is {age.days} days old, past the "
                    f"{UNKNOWN_REPLAY_WINDOW.days}-day replay window; the owner resolves it"
                )
        reasons += [
            f"key of attempt {a.id} reused by line {item.line_id} attempt {item.attempt_no}"
            for a in snap.attempts
            if (a.line_id, a.attempt_no) != (item.line_id, item.attempt_no)
            and {a.sender_batch_id, a.request_id, a.sender_item_id} & set(_keys(item))
        ]
    if reasons:
        return blocked(NAME, reasons)
    if owner:
        return needs_owner(NAME, owner)
    detail = "keys derived from line id and attempt number"
    if replays:
        detail += f"; {replays} identical replay(s) of UNKNOWN inside the window"
    return passed(NAME, detail)
