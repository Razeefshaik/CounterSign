import re
from itertools import product
from pathlib import Path

import pytest

from countersign.fsm import IllegalTransition
from countersign.fsm.incident import INCIDENT, IncidentEvent, IncidentState

DOCS = Path(__file__).resolve().parents[3] / "docs"

# PLAN section 2.3 gives the first four moves; the rest fill its "..." (see incident.py).
PLAN_TABLE = {
    ("OPEN", "INVESTIGATION_STARTED"): "INVESTIGATING",
    ("INVESTIGATING", "OWNER_NEEDED"): "AWAITING_OWNER",
    ("INVESTIGATING", "EXECUTION_STARTED"): "EXECUTING",
    ("INVESTIGATING", "WAIT_STARTED"): "WAITING_EXTERNAL",
    ("INVESTIGATING", "RECONCILED"): "RESOLVED",
    ("AWAITING_OWNER", "EXECUTION_STARTED"): "EXECUTING",
    ("AWAITING_OWNER", "OWNER_REJECTED"): "INVESTIGATING",
    ("AWAITING_OWNER", "APPROVAL_EXPIRED"): "INVESTIGATING",
    ("EXECUTING", "EXECUTION_SUCCEEDED"): "WAITING_EXTERNAL",
    ("EXECUTING", "RECHECK_FAILED"): "INVESTIGATING",
    ("EXECUTING", "EXECUTION_FAILED"): "INVESTIGATING",
    ("WAITING_EXTERNAL", "EXTERNAL_UPDATE"): "INVESTIGATING",
    ("WAITING_EXTERNAL", "RECONCILED"): "RESOLVED",
}
ILLEGAL = sorted(set(product(IncidentState, IncidentEvent)) - set(PLAN_TABLE))


def contracts_enum(label):
    text = (DOCS / "CONTRACTS.md").read_text(encoding="utf-8")
    line = next(line for line in text.splitlines() if f"**{label}:**" in line)
    return re.findall(r"`([A-Z_]+)`", line.split(f"**{label}:**", 1)[1].split("**", 1)[0])


def test_table_is_exactly_the_documented_one():
    assert dict(INCIDENT.table) == PLAN_TABLE


@pytest.mark.parametrize(("state", "event", "target"), [(*k, v) for k, v in PLAN_TABLE.items()])
def test_every_legal_move_reaches_its_target(state, event, target):
    assert INCIDENT.next(state, event) == target


@pytest.mark.parametrize(("state", "event"), ILLEGAL)
def test_every_other_pair_is_illegal(state, event):
    with pytest.raises(IllegalTransition):
        INCIDENT.next(state, event)


def test_states_match_the_dashboard_contract():
    # incidents.state has no CHECK in PLAN 2.2; CONTRACTS section 1 is the list the dashboard shows.
    assert [s.value for s in IncidentState] == contracts_enum("Incident states")


def test_plans_spine_is_present():
    assert INCIDENT.next("OPEN", "INVESTIGATION_STARTED") == "INVESTIGATING"
    targets = {INCIDENT.next("INVESTIGATING", e) for e in INCIDENT.allowed("INVESTIGATING")}
    assert {"AWAITING_OWNER", "EXECUTING", "WAITING_EXTERNAL"} <= targets


def test_resolved_only_when_the_reconciler_agrees():
    into_resolved = {key for key, target in INCIDENT.table.items() if target == "RESOLVED"}
    assert {event for _, event in into_resolved} == {"RECONCILED"}


@pytest.mark.parametrize("state", ["EXECUTING", "AWAITING_OWNER", "OPEN"])
def test_no_resolution_mid_write_mid_decision_or_uninvestigated(state):
    with pytest.raises(IllegalTransition):
        INCIDENT.next(state, IncidentEvent.RECONCILED)


def test_a_successful_write_still_waits_for_confirmation():
    assert INCIDENT.next("EXECUTING", "EXECUTION_SUCCEEDED") == "WAITING_EXTERNAL"


def test_resolved_is_final_and_every_other_state_can_reach_it():
    assert INCIDENT.allowed("RESOLVED") == frozenset()
    reach = {"RESOLVED"}
    while True:
        more = {s for (s, _), t in INCIDENT.table.items() if t in reach} - reach
        if not more:
            break
        reach |= more
    assert reach == set(IncidentState)
