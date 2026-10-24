"""State machines: one StrEnum of states, one of events, and a transition table per entity.

Every entity module (obligation, line, attempt, operation, proposal, incident) exposes a
Machine. Disputes have none: PayPal owns their status, and dispute.phase() derives what
CounterSign acts on from the mirrored fields. The database layer persists a move with
cas_transition(conn, machine, ..., expected_state, expected_version, event), which asks
machine.next() for the target state and raises before touching the row if the move is
illegal. PLAN section 2.3 is the source for every table.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum


class IllegalTransition(ValueError):
    """An event that the entity's transition table does not allow from its current state."""


@dataclass(frozen=True)
class Machine[S: StrEnum, E: StrEnum]:
    name: str
    states: type[S]
    events: type[E]
    table: Mapping[tuple[S, E], S]

    def __post_init__(self):
        for (state, event), target in self.table.items():
            if (
                not isinstance(state, self.states)
                or not isinstance(event, self.events)
                or not isinstance(target, self.states)
            ):
                raise TypeError(f"{self.name}: table entries must use its own state and event enums.")

    def next(self, state: S | str, event: E | str) -> S:
        """Target state for event from state; IllegalTransition when the table has no entry."""
        try:
            key = (self.states(state), self.events(event))
        except ValueError:
            raise IllegalTransition(f"{self.name}: unknown state or event.") from None
        if key not in self.table:
            raise IllegalTransition(f"{self.name}: {key[1]} is not allowed from {key[0]}.")
        return self.table[key]

    def allowed(self, state: S | str) -> frozenset[E]:
        """Events accepted from state, for dashboards and tests."""
        return frozenset(event for (source, event) in self.table if source == state)
