"""The engine: every rule outcome is returned, verdicts combine per PLAN 2.3, and it fails closed."""

import ast
import re
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest
from shield_world import (
    NOW,
    OBL_B,
    OP_C1,
    PROPOSAL,
    cash,
)

from countersign.fsm.obligation import ObligationState
from countersign.shield import RULES, Action, Proposal, Snapshot, evaluate
from countersign.shield.engine import Outcome, RuleName, RuleResult, Verdict, blocked, passed
from countersign.shield.snapshot import Line

ROOT = Path(__file__).parents[3]
SHIELD = ROOT / "src" / "countersign" / "shield"
CONTRACTS = (ROOT / "docs" / "CONTRACTS.md").read_text(encoding="utf-8")
SLICE = [
    "SETTLEMENT",
    "PAYOUT_STATE_MACHINE",
    "CANCEL_BEFORE_REPLACE",
    "IDEMPOTENCY",
    "WRITE_AHEAD",
    "DISPUTE_RESERVE",
    "FUNDING",
    "PAYEE_INTEGRITY",
    "UNKNOWN_SCOPED",
    "APPROVAL_BINDING",
]


def contracts_enum(label: str) -> list[str]:
    line = next(x for x in CONTRACTS.splitlines() if f"**{label}:**" in x)
    section = line.split(f"**{label}:**", 1)[1].split("**", 1)[0]
    return re.findall(r"`([A-Z_]+)`", section)


def settle(snap: Snapshot) -> Snapshot:
    return replace(
        snap,
        obligations=tuple(
            replace(o, state=ObligationState.SETTLED, version=o.version + 1) if o.id == OBL_B else o
            for o in snap.obligations
        ),
    )


# Names and shapes match CONTRACTS section 1 and 2.3.


def test_names_match_contracts():
    assert [r.value for r in RuleName] == contracts_enum("Rules")
    assert [v.value for v in Verdict] == contracts_enum("Verdicts")
    assert [o.value for o in Outcome] == contracts_enum("Rule outcomes")
    assert [a.value for a in Action] == contracts_enum("Actions")


def test_registry_holds_the_ten_slice_rules_in_contracts_order():
    assert [name.value for name, _ in RULES] == SLICE


def test_rule_results_are_exactly_the_contracts_item_shape(world):
    decision = evaluate(world.proposal(), world.run())
    for item in decision.rule_results():
        assert set(item) == {"rule", "outcome", "detail"}
        assert all(isinstance(v, str) and v for v in item.values())


# Every outcome is returned, PASS included.


def test_every_rule_outcome_is_returned_including_pass(world):
    decision = evaluate(world.proposal(), world.run())
    outcomes = {r.rule.value: r.outcome for r in decision.results}
    assert list(outcomes) == [r for r in SLICE if r != "WRITE_AHEAD"]
    assert decision.not_applicable == (RuleName.WRITE_AHEAD,)  # no operation before execution
    assert outcomes.pop("APPROVAL_BINDING") is Outcome.NEEDS_OWNER
    assert set(outcomes.values()) == {Outcome.PASS}


def test_all_ten_rules_run_at_execution(world):
    decision = evaluate(world.proposal(), world.executing())
    assert [r.rule.value for r in decision.results] == SLICE
    assert {r.outcome for r in decision.results} == {Outcome.PASS}
    assert decision.verdict is Verdict.ALLOW and decision.approval_applied


def test_a_blocked_proposal_still_lists_the_rules_that_passed(world):
    decision = evaluate(world.proposal(), settle(world.run()))
    assert decision.outcome_of(RuleName.SETTLEMENT) is Outcome.BLOCK
    assert decision.outcome_of(RuleName.FUNDING) is Outcome.PASS


# The demo's live safety moments.


def test_paying_an_already_settled_obligation_is_blocked_by_settlement(world):
    decision = evaluate(world.proposal(), settle(world.run()))
    assert decision.verdict is Verdict.BLOCK
    assert RuleName.SETTLEMENT in decision.rules


def test_settled_obligation_is_blocked_again_at_execution_even_if_approved(world):
    approved = world.executing()
    decision = evaluate(world.proposal(), settle(approved))
    assert decision.verdict is Verdict.BLOCK
    assert decision.rules == (
        RuleName.SETTLEMENT,
        RuleName.PAYOUT_STATE_MACHINE,  # SETTLED cannot take a new attempt
        RuleName.APPROVAL_BINDING,  # settling bumped the obligation's version
    )


def test_buyer_injection_is_blocked(world):
    # CONTRACTS 2.3 example: ACCEPT_CLAIM with refund_to; PAYEE_INTEGRITY blocks it.
    proposal = world.proposal(
        Action.ACCEPT_CLAIM,
        params={"paypal_dispute_id": "PP-D-27811", "refund_to": "attacker@example.com"},
    )
    decision = evaluate(proposal, world.run(payouts=()))
    assert decision.verdict is Verdict.BLOCK
    assert decision.rules == (RuleName.PAYEE_INTEGRITY,)
    assert decision.outcome_of(RuleName.APPROVAL_BINDING) is Outcome.NEEDS_OWNER


def test_autonomous_cancel_is_allowed_and_names_its_rules(world):
    proposal = world.proposal(Action.CANCEL_UNCLAIMED_PAYOUT, params={"payout_item_id": "5KUD"})
    decision = evaluate(proposal, world.cancel_c())
    assert decision.verdict is Verdict.ALLOW and not decision.approval_applied
    assert [r.rule for r in decision.results] == [
        RuleName.PAYOUT_STATE_MACHINE,
        RuleName.PAYEE_INTEGRITY,
        RuleName.UNKNOWN_SCOPED,
    ]
    snap = replace(
        world.cancel_c(),
        at_execution=True,
        operation=world.operation(kind="PAYOUT_CANCEL", id=OP_C1),
    )
    decision = evaluate(proposal, snap)
    assert decision.verdict is Verdict.ALLOW
    assert decision.outcome_of(RuleName.WRITE_AHEAD) is Outcome.PASS


# Combination rules.


def rule(name: RuleName, outcome: Outcome):
    return name, lambda p, s: RuleResult(name, outcome, f"{outcome}")


def test_any_block_wins_and_lists_every_failing_rule(world):
    rules = [
        rule(RuleName.SETTLEMENT, Outcome.BLOCK),
        rule(RuleName.FUNDING, Outcome.NEEDS_OWNER),
        rule(RuleName.PAYEE_INTEGRITY, Outcome.BLOCK),
        rule(RuleName.UNKNOWN_SCOPED, Outcome.PASS),
    ]
    decision = evaluate(world.proposal(), world.run(), rules)
    assert decision.verdict is Verdict.BLOCK
    assert decision.rules == (RuleName.SETTLEMENT, RuleName.PAYEE_INTEGRITY)
    assert len(decision.results) == 4


def test_needs_owner_without_block(world):
    rules = [rule(RuleName.FUNDING, Outcome.NEEDS_OWNER), rule(RuleName.SETTLEMENT, Outcome.PASS)]
    decision = evaluate(world.proposal(), world.run(), rules)
    assert decision.verdict is Verdict.NEEDS_OWNER and decision.rules == (RuleName.FUNDING,)


def test_needs_owner_becomes_allow_at_execution_only_with_a_bound_approval(world):
    rules = [
        rule(RuleName.FUNDING, Outcome.NEEDS_OWNER),
        rule(RuleName.APPROVAL_BINDING, Outcome.PASS),
    ]
    decision = evaluate(world.proposal(), world.executing(), rules)
    assert decision.verdict is Verdict.ALLOW and decision.approval_applied
    # Not at execution: the same results stay NEEDS_OWNER.
    snap = replace(world.executing(), at_execution=False)
    assert evaluate(world.proposal(), snap, rules).verdict is Verdict.NEEDS_OWNER
    # No approval row: stays NEEDS_OWNER even if the rule list claims a pass.
    snap = replace(world.executing(), approval=None)
    assert evaluate(world.proposal(), snap, rules).verdict is Verdict.NEEDS_OWNER


def test_a_stale_approval_blocks_at_execution(world):
    snap = world.executing()
    snap = replace(snap, approval=replace(snap.approval, used_at=NOW))
    decision = evaluate(world.proposal(), snap)
    assert decision.verdict is Verdict.BLOCK and decision.rules == (RuleName.APPROVAL_BINDING,)


def test_all_pass_is_allow(world):
    decision = evaluate(world.proposal(), world.run(), [rule(RuleName.SETTLEMENT, Outcome.PASS)])
    assert decision.verdict is Verdict.ALLOW and decision.rules == ()


# Fail closed.


def test_a_rule_that_crashes_blocks(world):
    def broken(p, s):
        raise KeyError("boom")

    decision = evaluate(world.proposal(), world.run(), [(RuleName.FUNDING, broken)])
    assert decision.verdict is Verdict.BLOCK
    assert decision.results[0].detail == "rule failed closed: KeyError"


def test_a_rule_reporting_under_another_name_blocks(world):
    def liar(p, s):
        return passed(RuleName.SETTLEMENT, "fine")

    decision = evaluate(world.proposal(), world.run(), [(RuleName.FUNDING, liar)])
    assert decision.verdict is Verdict.BLOCK and decision.rules == (RuleName.FUNDING,)


def test_a_paying_proposal_with_no_items_blocks(world):
    decision = evaluate(world.proposal(), world.run(payouts=()))
    assert decision.verdict is Verdict.BLOCK
    assert RuleName.SETTLEMENT in decision.rules


def test_unknown_action_cannot_be_proposed():
    with pytest.raises(ValueError):
        Proposal(PROPOSAL, "SEND_MONEY_NOW", {}, "sha256:x")


def test_snapshot_needs_aware_times():
    with pytest.raises(ValueError):
        Snapshot(now=datetime(2026, 11, 6, 9, 0), cash=cash())


def test_stored_state_strings_become_enums(world):
    line = Line("l", "r", "m", "UNPAID", world.pay_b().amount, 1)
    assert line.state is line.state.UNPAID


def test_evaluation_is_deterministic(world):
    proposal, snap = world.proposal(), world.executing()
    assert evaluate(proposal, snap) == evaluate(proposal, snap)
    assert blocked(RuleName.FUNDING, ["a", "b"]).detail == "a; b"


# Purity: the shield imports only money, fsm, ledger.cash and itself, and does no I/O.

ALLOWED_INTERNAL = ("countersign.money", "countersign.fsm", "countersign.ledger.cash")
STDLIB = {
    "collections",
    "dataclasses",
    "datetime",
    "enum",
    "functools",
    "re",
    "types",
    "typing",
    "uuid",
}


@pytest.mark.parametrize("path", sorted(SHIELD.rglob("*.py")), ids=lambda p: p.stem)
def test_shield_is_pure(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        elif isinstance(node, ast.Call) and getattr(node.func, "id", None) == "open":
            pytest.fail(f"{path.name} opens a file")
        else:
            continue
        for name in names:
            if name.startswith("countersign."):
                assert name.startswith((*ALLOWED_INTERNAL, "countersign.shield")), name
            else:
                assert name.split(".")[0] in STDLIB, name
