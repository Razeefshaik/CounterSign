import re
from itertools import product
from pathlib import Path

import pytest

from countersign.fsm import IllegalTransition
from countersign.fsm.operation import OPERATION, OperationEvent, OperationState

DOCS = Path(__file__).resolve().parents[3] / "docs"

# PLAN section 2.3, fsm/operation.py, copied by hand so a table edit cannot pass silently.
PLAN_TABLE = {
    ("PENDING", "LOCK_AND_RECHECK_OK"): "IN_FLIGHT",
    ("PENDING", "RECHECK_FAILED"): "BLOCKED",
    ("IN_FLIGHT", "OK"): "SUCCEEDED",
    ("IN_FLIGHT", "DEFINITE_ERROR"): "FAILED",
    ("IN_FLIGHT", "AMBIGUOUS"): "UNKNOWN",
    ("UNKNOWN", "RESOLVED_OK"): "SUCCEEDED",
    ("UNKNOWN", "RESOLVED_NOT_DONE"): "FAILED",
}
ILLEGAL = sorted(set(product(OperationState, OperationEvent)) - set(PLAN_TABLE))


def plan_check(table, column):
    plan = (DOCS / "PLAN.md").read_text(encoding="utf-8")
    block = plan.split(f"CREATE TABLE {table} (", 1)[1].split("CREATE TABLE", 1)[0]
    found = re.search(rf"{column} text (?:NOT NULL )?CHECK \({column} IN \(([^)]*)\)", block)
    return re.findall(r"'([A-Z_]+)'", found[1])


def test_table_is_exactly_plans():
    assert dict(OPERATION.table) == PLAN_TABLE


@pytest.mark.parametrize(("state", "event", "target"), [(*k, v) for k, v in PLAN_TABLE.items()])
def test_every_legal_move_reaches_its_target(state, event, target):
    assert OPERATION.next(state, event) == target


@pytest.mark.parametrize(("state", "event"), ILLEGAL)
def test_every_other_pair_is_illegal(state, event):
    with pytest.raises(IllegalTransition):
        OPERATION.next(state, event)


def test_states_match_the_plan_check_list():
    assert [s.value for s in OperationState] == plan_check("operations", "state")


def test_unknown_resolves_only_through_the_reconciler():
    assert OPERATION.allowed(OperationState.UNKNOWN) == {
        OperationEvent.RESOLVED_OK,
        OperationEvent.RESOLVED_NOT_DONE,
    }
    with pytest.raises(IllegalTransition):  # a late response is not a resolution
        OPERATION.next(OperationState.UNKNOWN, OperationEvent.OK)


@pytest.mark.parametrize("state", ["SUCCEEDED", "FAILED", "BLOCKED"])
def test_finished_operations_never_move_again(state):
    assert OPERATION.allowed(state) == frozenset()


def test_in_flight_is_reached_only_after_the_recheck():
    into_flight = {key for key, target in OPERATION.table.items() if target == "IN_FLIGHT"}
    assert into_flight == {("PENDING", "LOCK_AND_RECHECK_OK")}
