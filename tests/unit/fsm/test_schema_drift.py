"""The database's allowed states must equal the state machines', table by table.

Parses the migration SQL, so it runs without a database. If a migration and an enum ever
disagree, this fails before a row can hold a state the code cannot handle.
"""

import re
from pathlib import Path

import pytest

from countersign.db.transitions import CAS_TABLES
from countersign.fsm.attempt import LIVE, AttemptState
from countersign.fsm.dispute import Phase
from countersign.fsm.incident import IncidentState
from countersign.fsm.line import LineState
from countersign.fsm.obligation import ObligationState
from countersign.fsm.operation import OperationState
from countersign.fsm.proposal import ProposalState

ROOT = Path(__file__).resolve().parents[3] / "migrations"
SCHEMA = (ROOT / "sql" / "0001_schema.sql").read_text(encoding="utf-8")
GUARDS = (ROOT / "versions" / "0003_state_guards.py").read_text(encoding="utf-8")


def table_sql(table: str) -> str:
    match = re.search(rf"CREATE TABLE {table} \((.*?)\);", SCHEMA, re.DOTALL)
    assert match, f"{table} not found in 0001_schema.sql"
    return match.group(1)


def check_list(sql: str, column: str) -> set[str]:
    match = re.search(rf"\b{column} text NOT NULL CHECK \({column} IN \((.*?)\)\)", sql, re.DOTALL)
    assert match, f"no CHECK list for {column}"
    return set(re.findall(r"'([A-Z_]+)'", match.group(1)))


def guard_tuple(name: str) -> set[str]:
    match = re.search(rf"{name} = \((.*?)\)\n", GUARDS, re.DOTALL)
    assert match, f"{name} not found in 0003_state_guards.py"
    return set(re.findall(r'"([A-Z_]+)"', match.group(1)))


@pytest.mark.parametrize(
    ("table", "column", "enum"),
    [
        ("obligations", "state", ObligationState),
        ("payout_lines", "state", LineState),
        ("payout_attempts", "state", AttemptState),
        ("operations", "state", OperationState),
        ("disputes", "phase", Phase),
    ],
)
def test_schema_check_lists_equal_the_enums(table, column, enum):
    assert check_list(table_sql(table), column) == {member.value for member in enum}


@pytest.mark.parametrize(
    ("name", "enum"), [("PROPOSAL_STATES", ProposalState), ("INCIDENT_STATES", IncidentState)]
)
def test_guard_migration_lists_equal_the_enums(name, enum):
    assert guard_tuple(name) == {member.value for member in enum}


def test_one_live_attempt_index_covers_exactly_the_live_states():
    match = re.search(r"uq_one_live_attempt.*?WHERE state IN \((.*?)\);", SCHEMA, re.DOTALL)
    assert match
    assert set(re.findall(r"'([A-Z_]+)'", match.group(1))) == {state.value for state in LIVE}


def test_every_cas_table_has_a_version_column():
    match = re.search(r"VERSIONED = \((.*?)\)\n", GUARDS, re.DOTALL)
    assert match
    added = set(re.findall(r'"([a-z_]+)"', match.group(1)))
    for table in CAS_TABLES:
        assert "version int NOT NULL" in table_sql(table) or table in added, table
