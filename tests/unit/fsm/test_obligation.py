import re
from itertools import product
from pathlib import Path

import pytest

from countersign.fsm import IllegalTransition
from countersign.fsm.obligation import OBLIGATION, ObligationEvent, ObligationState

S, E = ObligationState, ObligationEvent
PLAN = Path(__file__).parents[3] / "docs" / "PLAN.md"

# PLAN section 2.3, fsm/obligation.py, copied by hand.
LEGAL = {
    (S.OPEN, E.DISPUTE_OPENED): S.RESERVED,
    (S.OPEN, E.LINE_APPROVED): S.SCHEDULED,
    (S.OPEN, E.ORDER_REFUNDED): S.VOID,
    (S.RESERVED, E.DISPUTE_WON): S.OPEN,
    (S.RESERVED, E.DISPUTE_CANCELLED): S.OPEN,
    (S.RESERVED, E.DISPUTE_LOST_PARTIAL): S.OPEN,
    (S.RESERVED, E.DISPUTE_LOST_FULL): S.VOID,
    (S.SCHEDULED, E.DISPUTE_OPENED): S.RESERVED,
    (S.SCHEDULED, E.LINE_RELEASED): S.OPEN,
    (S.SCHEDULED, E.ATTEMPT_PREPARED): S.IN_FLIGHT,
    (S.IN_FLIGHT, E.ATTEMPT_PAID): S.SETTLED,
    (S.IN_FLIGHT, E.ATTEMPT_NOT_PAID): S.SCHEDULED,
    (S.SETTLED, E.PAYOUT_CLAWED_BACK): S.OPEN,
}
ILLEGAL = sorted(set(product(S, E)) - set(LEGAL))


def plan_check_list(table: str) -> set[str]:
    """The state CHECK list for table in PLAN section 2.2."""
    block = PLAN.read_text(encoding="utf-8").split(f"CREATE TABLE {table} (", 1)[1]
    block = block.split("CREATE ", 1)[0]
    states = re.search(r"CHECK \(state IN \((.*?)\)\)", block, re.DOTALL).group(1)
    return set(re.findall(r"'([A-Z_]+)'", states))


def test_table_is_exactly_plan():
    assert OBLIGATION.table == LEGAL
    assert len(LEGAL) == 13


@pytest.mark.parametrize(("state", "event"), list(LEGAL))
def test_legal_moves_reach_plan_target(state, event):
    assert OBLIGATION.next(state, event) is LEGAL[(state, event)]


@pytest.mark.parametrize(("state", "event"), ILLEGAL)
def test_every_other_pair_raises(state, event):
    with pytest.raises(IllegalTransition):
        OBLIGATION.next(state, event)


def test_dispute_on_in_flight_money_has_no_transition():
    # The caller opens a PAID_BEFORE_DISPUTE incident instead.
    with pytest.raises(IllegalTransition):
        OBLIGATION.next(S.IN_FLIGHT, E.DISPUTE_OPENED)


def test_settled_only_comes_from_a_confirmed_paid_attempt():
    # UNCLAIMED keeps IN_FLIGHT: there is no other way into SETTLED.
    into_settled = [key for key, target in OBLIGATION.table.items() if target is S.SETTLED]
    assert into_settled == [(S.IN_FLIGHT, E.ATTEMPT_PAID)]


def test_void_is_terminal():
    assert OBLIGATION.allowed(S.VOID) == frozenset()


def test_states_equal_plan_check_list():
    assert {s.value for s in S} == plan_check_list("obligations")
