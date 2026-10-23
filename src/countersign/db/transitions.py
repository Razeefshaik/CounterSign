"""State-machine validation followed by an atomic compare-and-swap."""

from uuid import UUID

from psycopg import sql

from countersign.fsm import Machine

from .pool import Connection

# PLAN 2.2 only gives these state-bearing tables both id and version columns.
CAS_TABLES = frozenset({"obligations", "payout_runs", "payout_lines", "incidents"})


class StaleRow(RuntimeError):
    """The row was deleted or its expected state/version no longer matches."""


async def cas_transition(
    conn: Connection,
    machine: Machine,
    table: str,
    row_id: UUID,
    expected_state: str,
    expected_version: int,
    event: str,
) -> str:
    """Validate before SQL, then atomically change a versioned row's state."""
    new_state = machine.next(expected_state, event)
    if table not in CAS_TABLES:
        raise ValueError("CAS requires an approved table with state and version columns")
    if type(expected_version) is not int or expected_version < 1:
        raise ValueError("expected_version must be a positive integer")
    query = sql.SQL(
        "UPDATE {} SET state = %s, version = version + 1{} "
        "WHERE id = %s AND state = %s AND version = %s RETURNING state"
    ).format(
        sql.Identifier(table),
        sql.SQL(", updated_at = now()") if table == "obligations" else sql.SQL(""),
    )
    cursor = await conn.execute(
        query, (str(new_state), row_id, str(expected_state), expected_version)
    )
    if await cursor.fetchone() is None:
        raise StaleRow("State or version changed, or the row no longer exists")
    return new_state
