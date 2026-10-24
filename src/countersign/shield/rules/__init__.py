"""The rule registry: pure functions (proposal, snapshot) -> RuleResult | None, in CONTRACTS order.

The ten C17 slice rules. REFUND_POLICY, EVIDENCE_INTEGRITY, DEADLINE_GUARD, RECHECK_AT_EXECUTION
and WEBHOOK_AUTHENTICITY come after the slice (RECHECK_AT_EXECUTION is the executor running this
same registry with at_execution set).
"""

from countersign.shield.engine import Rule, RuleName
from countersign.shield.rules import (
    approval_binding,
    cancel_before_replace,
    dispute_reserve,
    funding,
    idempotency,
    payee_integrity,
    payout_state_machine,
    settlement,
    unknown_scoped,
    write_ahead,
)

RULES: tuple[tuple[RuleName, Rule], ...] = tuple(
    (module.NAME, module.check)
    for module in (
        settlement,
        payout_state_machine,
        cancel_before_replace,
        idempotency,
        write_ahead,
        dispute_reserve,
        funding,
        payee_integrity,
        unknown_scoped,
        approval_binding,
    )
)
