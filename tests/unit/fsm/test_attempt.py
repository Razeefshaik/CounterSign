import re
from datetime import timedelta
from itertools import product
from pathlib import Path

import pytest

from countersign.fsm import IllegalTransition
from countersign.fsm.attempt import (
    ATTEMPT,
    LIVE,
    PAYPAL_EXPECTED,
    PAYPAL_STATUSES,
    UNKNOWN_REPLAY_WINDOW,
    WEBHOOK_TO_STATUS,
    AttemptEvent,
    AttemptState,
    mirror,
)

S, E = AttemptState, AttemptEvent
PLAN = Path(__file__).parents[3] / "docs" / "PLAN.md"

# PLAN section 2.3, fsm/attempt.py, copied by hand.
LEGAL = {
    (S.PREPARED, E.HTTP_2XX): S.SUBMITTED,
    (S.PREPARED, E.HTTP_DEFINITE_4XX): S.REJECTED,
    (S.PREPARED, E.AMBIGUOUS): S.UNKNOWN,
    (S.UNKNOWN, E.REPLAY_CONFIRMED): S.SUBMITTED,
}
ILLEGAL = sorted(set(product(S, E)) - set(LEGAL))

EXPECTED = {
    S.PENDING: {S.SUCCESS, S.FAILED, S.UNCLAIMED, S.ONHOLD, S.BLOCKED},
    S.ONHOLD: {S.PENDING, S.SUCCESS, S.FAILED, S.BLOCKED, S.RETURNED},
    S.UNCLAIMED: {S.SUCCESS, S.RETURNED},
    S.SUCCESS: {S.REFUNDED, S.REVERSED},
}
PAYPAL = {S.PENDING, S.UNCLAIMED, S.ONHOLD, S.BLOCKED} | {
    S.SUCCESS,
    S.FAILED,
    S.RETURNED,
    S.REFUNDED,
    S.REVERSED,
}
OURS = set(S) - PAYPAL
MIRROR_MOVES = [(a, b) for a, b in product(PAYPAL, PAYPAL) if a is not b]
EXPECTED_MOVES = sorted((a, b) for a, b in MIRROR_MOVES if b in EXPECTED.get(a, ()))
UNEXPECTED_MOVES = sorted((a, b) for a, b in MIRROR_MOVES if b not in EXPECTED.get(a, ()))


def plan_sql() -> str:
    return PLAN.read_text(encoding="utf-8")


def quoted(text: str) -> set[str]:
    return set(re.findall(r"'([A-Z_]+)'", text))


# Machine ATTEMPT: our four moves.


def test_table_is_exactly_plan():
    assert ATTEMPT.table == LEGAL


@pytest.mark.parametrize(("state", "event"), list(LEGAL))
def test_legal_moves_reach_plan_target(state, event):
    assert ATTEMPT.next(state, event) is LEGAL[(state, event)]


@pytest.mark.parametrize(("state", "event"), ILLEGAL)
def test_every_other_pair_raises(state, event):
    with pytest.raises(IllegalTransition):
        ATTEMPT.next(state, event)


def test_paypal_states_never_move_through_our_machine():
    for state in PAYPAL:
        assert ATTEMPT.allowed(state) == frozenset()


# mirror(): from SUBMITTED onward the state follows PayPal's GET.


def test_paypal_expected_is_exactly_plan():
    assert PAYPAL_EXPECTED == EXPECTED
    assert PAYPAL_STATUSES == PAYPAL


@pytest.mark.parametrize(("current", "status"), EXPECTED_MOVES)
def test_expected_mirror_moves(current, status):
    assert mirror(current, status) == (status, True)


@pytest.mark.parametrize(("current", "status"), UNEXPECTED_MOVES)
def test_unexpected_mirror_moves_are_kept_and_flagged(current, status):
    # Recorded, never dropped; expected=False tells the caller to open an incident.
    assert mirror(current, status) == (status, False)


@pytest.mark.parametrize("status", sorted(PAYPAL))
def test_first_mirror_from_submitted_records_any_status_but_flags_ones_needing_history(status):
    needs_history = {S.RETURNED, S.REFUNDED, S.REVERSED}
    assert mirror(S.SUBMITTED, status) == (status, status not in needs_history)


@pytest.mark.parametrize("status", sorted(PAYPAL))
def test_seeing_the_same_status_again_is_not_a_move(status):
    assert mirror(status, status) == (status, True)


def test_mirror_accepts_plain_strings():
    assert mirror("PENDING", "SUCCESS") == (S.SUCCESS, True)


@pytest.mark.parametrize("current", sorted(OURS - {S.SUBMITTED}))
def test_mirror_refuses_attempts_that_paypal_has_not_seen(current):
    # PREPARED and UNKNOWN have no item id to GET; REJECTED never reached PayPal.
    with pytest.raises(IllegalTransition):
        mirror(current, S.SUCCESS)


@pytest.mark.parametrize("status", [*sorted(OURS), "CANCELED", "DENIED", "success"])
def test_mirror_refuses_values_that_are_not_item_statuses(status):
    with pytest.raises(ValueError):
        mirror(S.PENDING, status)


def test_unclaimed_is_not_success():
    # UNCLAIMED keeps the obligation IN_FLIGHT; only SUCCESS settles it.
    state, expected = mirror(S.PENDING, S.UNCLAIMED)
    assert (state, expected) == (S.UNCLAIMED, True)
    assert state in LIVE


# Constants.


def test_live_equals_the_unique_index_in_plan():
    index = plan_sql().split("CREATE UNIQUE INDEX uq_one_live_attempt", 1)[1].split(";", 1)[0]
    assert {s.value for s in LIVE} == quoted(index)


def test_states_equal_plan_check_list():
    block = plan_sql().split("CREATE TABLE payout_attempts (", 1)[1].split("CREATE ", 1)[0]
    states = re.search(r"CHECK \(state IN \((.*?)\)\)", block, re.DOTALL).group(1)
    assert {s.value for s in S} == quoted(states)


def test_webhook_to_status_covers_every_item_webhook():
    # CONTRACTS section 5 (mock_paypal webhooks): PAYMENT.PAYOUTS-ITEM.<suffix> names.
    assert WEBHOOK_TO_STATUS == {
        "SUCCEEDED": S.SUCCESS,
        "HELD": S.ONHOLD,
        "UNCLAIMED": S.UNCLAIMED,
        "FAILED": S.FAILED,
        "BLOCKED": S.BLOCKED,
        "RETURNED": S.RETURNED,
        "REFUNDED": S.REFUNDED,
        "CANCELED": None,
        "DENIED": None,
    }
    assert {v for v in WEBHOOK_TO_STATUS.values() if v is not None} <= PAYPAL


def test_unknown_replay_window_is_inside_paypals_30_days():
    assert UNKNOWN_REPLAY_WINDOW == timedelta(days=25)
