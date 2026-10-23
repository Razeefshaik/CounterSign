import re
from itertools import product
from pathlib import Path

import pytest

from countersign.fsm import IllegalTransition
from countersign.fsm.proposal import PROPOSAL, VERDICT_EVENT, ProposalEvent, ProposalState

DOCS = Path(__file__).resolve().parents[3] / "docs"

# PLAN section 2.3, fsm/proposal.py:
# CREATED -> ALLOWED | BLOCKED | NEEDS_OWNER;  NEEDS_OWNER -> APPROVED | REJECTED | EXPIRED;
# ALLOWED|APPROVED -> EXECUTING -> EXECUTED | BLOCKED_AT_EXECUTION | FAILED
PLAN_TABLE = {
    ("CREATED", "VERDICT_ALLOW"): "ALLOWED",
    ("CREATED", "VERDICT_BLOCK"): "BLOCKED",
    ("CREATED", "VERDICT_NEEDS_OWNER"): "NEEDS_OWNER",
    ("NEEDS_OWNER", "OWNER_APPROVED"): "APPROVED",
    ("NEEDS_OWNER", "OWNER_REJECTED"): "REJECTED",
    ("NEEDS_OWNER", "APPROVAL_EXPIRED"): "EXPIRED",
    ("ALLOWED", "EXECUTION_STARTED"): "EXECUTING",
    ("APPROVED", "EXECUTION_STARTED"): "EXECUTING",
    ("EXECUTING", "EXECUTION_SUCCEEDED"): "EXECUTED",
    ("EXECUTING", "RECHECK_FAILED"): "BLOCKED_AT_EXECUTION",
    ("EXECUTING", "EXECUTION_FAILED"): "FAILED",
}
ILLEGAL = sorted(set(product(ProposalState, ProposalEvent)) - set(PLAN_TABLE))


def contracts_enum(label):
    text = (DOCS / "CONTRACTS.md").read_text(encoding="utf-8")
    line = next(line for line in text.splitlines() if f"**{label}:**" in line)
    return re.findall(r"`([A-Z_]+)`", line.split(f"**{label}:**", 1)[1].split("**", 1)[0])


def plan_check(table, column):
    plan = (DOCS / "PLAN.md").read_text(encoding="utf-8")
    block = plan.split(f"CREATE TABLE {table} (", 1)[1].split("CREATE TABLE", 1)[0]
    found = re.search(rf"{column} text (?:NOT NULL )?CHECK \({column} IN \(([^)]*)\)", block)
    return re.findall(r"'([A-Z_]+)'", found[1])


def test_table_is_exactly_plans():
    assert dict(PROPOSAL.table) == PLAN_TABLE


@pytest.mark.parametrize(("state", "event", "target"), [(*k, v) for k, v in PLAN_TABLE.items()])
def test_every_legal_move_reaches_its_target(state, event, target):
    assert PROPOSAL.next(state, event) == target


@pytest.mark.parametrize(("state", "event"), ILLEGAL)
def test_every_other_pair_is_illegal(state, event):
    with pytest.raises(IllegalTransition):
        PROPOSAL.next(state, event)


def test_states_match_the_dashboard_contract():
    # proposals.state has no CHECK in PLAN 2.2; CONTRACTS section 1 is the list the dashboard shows.
    assert [s.value for s in ProposalState] == contracts_enum("Proposal states")


def test_verdict_events_cover_exactly_the_plan_and_contract_verdicts():
    assert list(VERDICT_EVENT) == plan_check("proposals", "verdict")
    assert list(VERDICT_EVENT) == contracts_enum("Verdicts")
    landing = {"ALLOW": "ALLOWED", "BLOCK": "BLOCKED", "NEEDS_OWNER": "NEEDS_OWNER"}
    for verdict, event in VERDICT_EVENT.items():
        assert PROPOSAL.next(ProposalState.CREATED, event) == landing[verdict]


def test_only_allowed_or_approved_proposals_can_execute():
    starts = {key[0] for key, target in PROPOSAL.table.items() if target == "EXECUTING"}
    assert starts == {"ALLOWED", "APPROVED"}
    for state in ("CREATED", "BLOCKED", "NEEDS_OWNER", "REJECTED", "EXPIRED"):
        with pytest.raises(IllegalTransition):
            PROPOSAL.next(state, ProposalEvent.EXECUTION_STARTED)


def test_owner_decisions_apply_only_while_needs_owner():
    for event in ("OWNER_APPROVED", "OWNER_REJECTED", "APPROVAL_EXPIRED"):
        assert {key[0] for key in PROPOSAL.table if key[1] == event} == {"NEEDS_OWNER"}


@pytest.mark.parametrize(
    "state", ["BLOCKED", "REJECTED", "EXPIRED", "EXECUTED", "BLOCKED_AT_EXECUTION", "FAILED"]
)
def test_finished_proposals_never_move_again(state):
    assert PROPOSAL.allowed(state) == frozenset()
