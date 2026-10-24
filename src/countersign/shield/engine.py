"""The shield engine: run every rule over a proposal and its snapshot, then combine (PLAN 2.3).

Rules are pure functions (proposal, snapshot) -> RuleResult | None. None means the rule does not
apply to this action, so it was not evaluated. Every evaluated rule's result is kept, PASS
included (CONTRACTS 2.3 rule_results). Combination:
- any BLOCK -> BLOCK, naming every blocking rule;
- else any NEEDS_OWNER -> NEEDS_OWNER, which at execution becomes ALLOW only when
  APPROVAL_BINDING passed on a bound approval;
- else ALLOW.
A rule that raises is recorded as BLOCK: a crash never lets money move.
"""

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from countersign.money import Money
from countersign.shield.snapshot import Proposal, Snapshot


class Verdict(StrEnum):
    ALLOW = "ALLOW"
    BLOCK = "BLOCK"
    NEEDS_OWNER = "NEEDS_OWNER"


class Outcome(StrEnum):
    PASS = "PASS"
    BLOCK = "BLOCK"
    NEEDS_OWNER = "NEEDS_OWNER"


class RuleName(StrEnum):
    """Every rule name in CONTRACTS section 1; the slice implements the first ten listed in C17."""

    SETTLEMENT = "SETTLEMENT"
    PAYOUT_STATE_MACHINE = "PAYOUT_STATE_MACHINE"
    CANCEL_BEFORE_REPLACE = "CANCEL_BEFORE_REPLACE"
    IDEMPOTENCY = "IDEMPOTENCY"
    WRITE_AHEAD = "WRITE_AHEAD"
    DISPUTE_RESERVE = "DISPUTE_RESERVE"
    FUNDING = "FUNDING"
    PAYEE_INTEGRITY = "PAYEE_INTEGRITY"
    REFUND_POLICY = "REFUND_POLICY"
    EVIDENCE_INTEGRITY = "EVIDENCE_INTEGRITY"
    DEADLINE_GUARD = "DEADLINE_GUARD"
    UNKNOWN_SCOPED = "UNKNOWN_SCOPED"
    APPROVAL_BINDING = "APPROVAL_BINDING"
    RECHECK_AT_EXECUTION = "RECHECK_AT_EXECUTION"
    WEBHOOK_AUTHENTICITY = "WEBHOOK_AUTHENTICITY"
    INJECTION_GUARD = "INJECTION_GUARD"


@dataclass(frozen=True)
class RuleResult:
    """One rule's outcome. detail is the CONTRACTS one-line reason; facts carry structured data
    for the audit log and are never part of the dashboard rule_results item."""

    rule: RuleName
    outcome: Outcome
    detail: str
    facts: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    def to_dict(self) -> dict[str, str]:
        """The CONTRACTS 2.3 rule_results item: exactly rule, outcome and detail."""
        return {"rule": str(self.rule), "outcome": str(self.outcome), "detail": self.detail}


def money_text(amount: Money) -> str:
    """Money as rule details print it, e.g. 1086.00 USD."""
    return f"{amount.to_paypal('currency')['value']} {amount.currency}"


def passed(rule: RuleName, detail: str, **facts: Any) -> RuleResult:
    return RuleResult(rule, Outcome.PASS, detail, MappingProxyType(facts))


def blocked(rule: RuleName, reasons: Iterable[str], **facts: Any) -> RuleResult:
    return RuleResult(rule, Outcome.BLOCK, "; ".join(reasons), MappingProxyType(facts))


def needs_owner(rule: RuleName, reasons: Iterable[str], **facts: Any) -> RuleResult:
    return RuleResult(rule, Outcome.NEEDS_OWNER, "; ".join(reasons), MappingProxyType(facts))


Rule = Callable[[Proposal, Snapshot], RuleResult | None]


@dataclass(frozen=True)
class Decision:
    verdict: Verdict
    results: tuple[RuleResult, ...]  # every evaluated rule, PASS included, in registry order
    rules: tuple[RuleName, ...]  # the rules that decided a BLOCK or NEEDS_OWNER verdict
    not_applicable: tuple[RuleName, ...]  # registered rules that did not apply to this action
    at_execution: bool
    approval_applied: bool = False  # ALLOW at execution rests on a valid bound approval

    def rule_results(self) -> list[dict[str, str]]:
        """CONTRACTS 2.3 rule_results, stored in proposals.verdict_details."""
        return [r.to_dict() for r in self.results]

    def outcome_of(self, rule: RuleName) -> Outcome | None:
        return next((r.outcome for r in self.results if r.rule is rule), None)


def evaluate(
    proposal: Proposal, snapshot: Snapshot, rules: Iterable[tuple[RuleName, Rule]] | None = None
) -> Decision:
    """Run every rule (default: the registry in shield.rules) and combine per PLAN 2.3."""
    if rules is None:
        from countersign.shield.rules import RULES  # rules import this module's types

        rules = RULES
    results: list[RuleResult] = []
    skipped: list[RuleName] = []
    for name, rule in rules:
        try:
            result = rule(proposal, snapshot)
        except Exception as exc:  # noqa: BLE001 - fail closed on any rule crash
            result = blocked(name, [f"rule failed closed: {type(exc).__name__}"], error=str(exc))
        if result is None:
            skipped.append(name)
        elif result.rule is not name:
            results.append(blocked(name, [f"rule reported as {result.rule}"]))
        else:
            results.append(result)

    def named(outcome: Outcome) -> tuple[RuleName, ...]:
        return tuple(r.rule for r in results if r.outcome is outcome)

    blocking, owner = named(Outcome.BLOCK), named(Outcome.NEEDS_OWNER)
    at_execution = snapshot.at_execution
    bound = (
        at_execution
        and snapshot.approval is not None
        and RuleName.APPROVAL_BINDING in named(Outcome.PASS)
    )
    if blocking:
        verdict, deciding = Verdict.BLOCK, blocking
    elif owner and not bound:
        verdict, deciding = Verdict.NEEDS_OWNER, owner
    else:
        verdict, deciding = Verdict.ALLOW, ()
    return Decision(
        verdict,
        results=tuple(results),
        rules=deciding,
        not_applicable=tuple(skipped),
        at_execution=at_execution,
        approval_applied=verdict is Verdict.ALLOW and bound,
    )
