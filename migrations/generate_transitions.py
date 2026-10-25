"""Generate the frozen 0004 transition rows from the real Python state machines.

Run with PYTHONPATH=src: python migrations/generate_transitions.py [--check].
A revision reads the checked-in output, not mutable application code, so its
meaning is reproducible. Regenerate before 0004 ships; later changes need a new revision.
"""

import argparse
import json
from pathlib import Path

from countersign.fsm.attempt import ATTEMPT
from countersign.fsm.incident import INCIDENT
from countersign.fsm.line import LINE
from countersign.fsm.obligation import OBLIGATION
from countersign.fsm.operation import OPERATION
from countersign.fsm.proposal import PROPOSAL

OUTPUT = Path(__file__).with_name("sql") / "0004_allowed_transitions.json"
MACHINES = (OBLIGATION, LINE, ATTEMPT, OPERATION, PROPOSAL, INCIDENT)


def generate_rows():
    """Events collapse to unique (entity, source, target) pairs for the DB primary key."""
    return tuple(
        sorted(
            {
                (machine.name, str(source), str(machine.next(source, event)))
                for machine in MACHINES
                for source, event in machine.table
            }
        )
    )


def render():
    return "[\n" + ",\n".join("  " + json.dumps(row) for row in generate_rows()) + "\n]\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if the frozen rows drift")
    args = parser.parse_args()
    expected = render()
    if args.check:
        if OUTPUT.read_text(encoding="utf-8") != expected:
            raise SystemExit("0004 transition snapshot differs from the Python FSM tables")
    else:
        OUTPUT.write_text(expected, encoding="utf-8")


if __name__ == "__main__":
    main()
