"""The rule registry: pure functions (proposal, snapshot) -> RuleResult | None, in CONTRACTS order.

The C17 slice rules, C23 INJECTION_GUARD and C24 dispute rules.
RECHECK_AT_EXECUTION and WEBHOOK_AUTHENTICITY are enforced at their service boundaries
(RECHECK_AT_EXECUTION is the executor running this same registry with at_execution set).
"""

from countersign.shield.engine import Rule, RuleName
from countersign.shield.rules import (
    approval_binding,
    cancel_before_replace,
    deadline_guard,
    dispute_reserve,
    evidence_integrity,
    funding,
    idempotency,
    injection_guard,
    payee_integrity,
    payout_state_machine,
    refund_policy,
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
        refund_policy,
        evidence_integrity,
        deadline_guard,
        unknown_scoped,
        approval_binding,
        injection_guard,
    )
)
