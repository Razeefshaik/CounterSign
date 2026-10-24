"""The safety shield: a deterministic, pure rule engine over a proposal and a loaded snapshot.

The AI proposes; this code countersigns. evaluate() returns ALLOW, BLOCK or NEEDS_OWNER with every
evaluated rule's outcome. No I/O: the caller loads the snapshot (PLAN 2.3, HANDOFF section 7).
"""

from countersign.shield.engine import (
    Decision,
    Outcome,
    RuleName,
    RuleResult,
    Verdict,
    evaluate,
)
from countersign.shield.rules import RULES
from countersign.shield.rules.idempotency import derive_keys
from countersign.shield.snapshot import Action, Proposal, Snapshot

__all__ = [
    "RULES",
    "Action",
    "Decision",
    "Outcome",
    "Proposal",
    "RuleName",
    "RuleResult",
    "Snapshot",
    "Verdict",
    "derive_keys",
    "evaluate",
]
