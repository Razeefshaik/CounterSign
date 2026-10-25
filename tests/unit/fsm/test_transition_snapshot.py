"""The frozen migration output must track the Python tables, not a handwritten allowlist."""

import json
from pathlib import Path

from countersign.fsm.attempt import OURS
from countersign.fsm.incident import TABLE as INCIDENT
from countersign.fsm.line import TABLE as LINE
from countersign.fsm.obligation import TABLE as OBLIGATION
from countersign.fsm.obligation import ObligationEvent, ObligationState
from countersign.fsm.operation import TABLE as OPERATION
from countersign.fsm.proposal import TABLE as PROPOSAL
from migrations import generate_transitions as generator

SNAPSHOT = Path(__file__).resolve().parents[3] / "migrations/sql/0004_allowed_transitions.json"


def frozen_rows():
    return [tuple(row) for row in json.loads(SNAPSHOT.read_text(encoding="utf-8"))]


def test_frozen_generated_rows_equal_all_six_python_tables_exactly():
    tables = {
        "obligation": OBLIGATION,
        "line": LINE,
        "attempt": OURS,
        "operation": OPERATION,
        "proposal": PROPOSAL,
        "incident": INCIDENT,
    }
    expected = {
        (name, str(source), str(target))
        for name, table in tables.items()
        for (source, _), target in table.items()
    }
    frozen = frozen_rows()
    assert set(frozen) == expected == set(generator.generate_rows())
    assert len(frozen) == len(expected)  # no duplicate pairs hidden by set comparison
    assert frozen == sorted(frozen)
    assert SNAPSHOT.read_text(encoding="utf-8") == generator.render()


def test_adding_a_python_transition_without_regeneration_exposes_drift(monkeypatch):
    monkeypatch.setitem(
        OBLIGATION, (ObligationState.VOID, ObligationEvent.LINE_APPROVED), ObligationState.SCHEDULED
    )
    assert set(generator.generate_rows()) != set(frozen_rows())


def test_adding_or_removing_a_frozen_row_exposes_drift():
    expected = set(generator.generate_rows())
    assert expected != set(frozen_rows()[:-1])
    assert expected != set(frozen_rows()) | {("obligation", "VOID", "OPEN")}
