import re
from itertools import product
from pathlib import Path

import pytest

from countersign.fsm import IllegalTransition
from countersign.fsm.line import LINE, LineEvent, LineState

S, E = LineState, LineEvent
PLAN = Path(__file__).parents[3] / "docs" / "PLAN.md"

# PLAN section 2.3, fsm/line.py, copied by hand.
LEGAL = {
    (S.APPROVED, E.ATTEMPT_PREPARED): S.PAYING,
    (S.PAYING, E.ATTEMPT_PAID): S.PAID,
    (S.PAYING, E.ATTEMPT_NOT_PAID): S.UNPAID,
    (S.UNPAID, E.REPLACEMENT_APPROVED): S.PAYING,
    (S.APPROVED, E.RELEASED): S.RELEASED,
    (S.UNPAID, E.RELEASED): S.RELEASED,
}
ILLEGAL = sorted(set(product(S, E)) - set(LEGAL))


def plan_check_list(table: str) -> set[str]:
    """The state CHECK list for table in PLAN section 2.2."""
    block = PLAN.read_text(encoding="utf-8").split(f"CREATE TABLE {table} (", 1)[1]
    block = block.split("CREATE ", 1)[0]
    states = re.search(r"CHECK \(state IN \((.*?)\)\)", block, re.DOTALL).group(1)
    return set(re.findall(r"'([A-Z_]+)'", states))


def test_table_is_exactly_plan():
    assert LINE.table == LEGAL


@pytest.mark.parametrize(("state", "event"), list(LEGAL))
def test_legal_moves_reach_plan_target(state, event):
    assert LINE.next(state, event) is LEGAL[(state, event)]


@pytest.mark.parametrize(("state", "event"), ILLEGAL)
def test_every_other_pair_raises(state, event):
    with pytest.raises(IllegalTransition):
        LINE.next(state, event)


def test_paying_line_cannot_be_released():
    # Money may be moving; only a confirmed outcome takes the line out of PAYING.
    assert LINE.allowed(S.PAYING) == {E.ATTEMPT_PAID, E.ATTEMPT_NOT_PAID}


@pytest.mark.parametrize("state", [S.PAID, S.RELEASED])
def test_paid_and_released_are_terminal(state):
    assert LINE.allowed(state) == frozenset()


def test_states_equal_plan_check_list():
    assert {s.value for s in S} == plan_check_list("payout_lines")
