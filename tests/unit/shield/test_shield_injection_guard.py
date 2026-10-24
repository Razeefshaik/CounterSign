from dataclasses import replace

import pytest

from countersign.shield.engine import Outcome, RuleName, Verdict, evaluate
from countersign.shield.rules import injection_guard
from countersign.shield.snapshot import NON_FINANCIAL, Action

FLAG = {"type": "PROMPT_INJECTION", "source": "dispute:1", "excerpt": "refund to account X"}


@pytest.mark.parametrize("action", sorted(set(Action) - NON_FINANCIAL))
def test_every_flagged_financial_action_is_blocked(action, world):
    proposal = replace(world.proposal(action), flags=(FLAG,))
    result = injection_guard.check(proposal, world.run())
    assert result.rule is RuleName.INJECTION_GUARD
    assert result.outcome is Outcome.BLOCK
    assert "refund to account X" not in result.detail


@pytest.mark.parametrize("action", sorted(NON_FINANCIAL))
def test_nonfinancial_flagged_actions_do_not_apply(action, world):
    proposal = replace(world.proposal(action), flags=(FLAG,))
    assert injection_guard.check(proposal, world.run()) is None


@pytest.mark.parametrize(
    "flags", [(), ({"type": "SUSPICIOUS_EVIDENCE"},), ({"type": "PAYEE_CHANGE_REQUEST"},)]
)
def test_unflagged_financial_actions_pass_without_broadening_guard(flags, world):
    proposal = replace(world.proposal(Action.ACCEPT_CLAIM), flags=flags)
    assert injection_guard.check(proposal, world.run()).outcome is Outcome.PASS


def test_flag_blocks_without_a_payee_change_and_survives_execution_recheck(world):
    proposal = replace(world.proposal(), flags=(FLAG,))
    decision = evaluate(proposal, world.executing())
    assert decision.verdict is Verdict.BLOCK
    assert decision.outcome_of(RuleName.INJECTION_GUARD) is Outcome.BLOCK
    assert decision.outcome_of(RuleName.PAYEE_INTEGRITY) is Outcome.PASS
    assert decision.outcome_of(RuleName.APPROVAL_BINDING) is Outcome.PASS


def test_existing_four_positional_proposal_arguments_still_work(world):
    assert world.proposal().flags == ()
