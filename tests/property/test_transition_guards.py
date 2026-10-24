"""Raw SQL cannot bypass application state machines, including with a changed search path."""

import json
from pathlib import Path

import psycopg
import pytest
from hardening_world import TABLES, seed, state, update
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st
from psycopg import sql

from migrations.generate_transitions import MACHINES, generate_rows

pytestmark = pytest.mark.integration
PAIRS = set(generate_rows())
ILLEGAL = tuple(
    (m.name, str(source), str(target))
    for m in MACHINES
    for source in m.states
    for target in m.states
    if source != target
    and (m.name, str(source), str(target)) not in PAIRS
    and (m.name != "attempt" or source in {"PREPARED", "UNKNOWN"})
)


def test_installed_rows_and_trigger_attachments_match_generated_fsm(db_schema):
    frozen = json.loads(
        (
            Path(__file__).resolve().parents[2] / "migrations/sql/0004_allowed_transitions.json"
        ).read_text()
    )
    with db_schema.connect() as conn:
        rows = conn.execute(
            "SELECT entity,from_state,to_state FROM allowed_transitions ORDER BY 1,2,3"
        ).fetchall()
        assert (
            [tuple(r.values()) for r in rows]
            == [tuple(row) for row in frozen]
            == list(generate_rows())
        )
        triggers = conn.execute(
            "SELECT c.relname,t.tgfoid FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid "
            "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s AND t.tgname='guard_state_transition'",
            (db_schema.schema,),
        ).fetchall()
        assert {r["relname"] for r in triggers} == set(TABLES.values())
        assert len({r["tgfoid"] for r in triggers}) == 1
        keys = conn.execute(
            "SELECT a.attname FROM pg_index i JOIN pg_class c ON c.oid=i.indrelid "
            "JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum=ANY(i.indkey) "
            "WHERE n.nspname=%s AND c.relname='allowed_transitions' AND i.indisprimary ORDER BY a.attnum",
            (db_schema.schema,),
        ).fetchall()
        assert [r["attname"] for r in keys] == ["entity", "from_state", "to_state"]


@settings(
    max_examples=120, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(move=st.sampled_from(ILLEGAL))
@example(move=("obligation", "OPEN", "SETTLED"))
@example(move=("line", "APPROVED", "PAID"))
@example(move=("attempt", "PREPARED", "SUCCESS"))
@example(move=("operation", "PENDING", "SUCCEEDED"))
@example(move=("proposal", "CREATED", "EXECUTED"))
@example(move=("incident", "OPEN", "RESOLVED"))
def test_illegal_raw_sql_state_moves_are_rejected_without_changing_rows(db_schema, move):
    entity, source, target = move
    with db_schema.connect() as conn, conn.transaction(force_rollback=True):
        ids = seed(conn, {entity: source})
        with pytest.raises(psycopg.errors.CheckViolation) as error, conn.transaction():
            update(conn, entity, ids[entity], target)
        assert error.value.diag.constraint_name == "allowed_transitions"
        assert state(conn, entity, ids[entity]) == source


@pytest.mark.parametrize("move", sorted(PAIRS))
def test_every_generated_transition_and_same_state_update_passes(db_schema, move):
    entity, source, target = move
    with db_schema.connect() as conn, conn.transaction(force_rollback=True):
        ids = seed(conn, {entity: source})
        update(conn, entity, ids[entity], source)
        update(conn, entity, ids[entity], target)
        assert state(conn, entity, ids[entity]) == target


def test_trigger_uses_its_own_schema_not_a_shadowed_allowlist(db_schema):
    with db_schema.connect() as conn, conn.transaction(force_rollback=True):
        ids = seed(conn)
        conn.execute(
            "CREATE TEMP TABLE allowed_transitions (entity text,from_state text,to_state text)"
        )
        conn.execute(
            "INSERT INTO pg_temp.allowed_transitions VALUES ('obligation','OPEN','SETTLED')"
        )
        conn.execute(
            sql.SQL("SET LOCAL search_path=pg_temp,{},public").format(
                sql.Identifier(db_schema.schema)
            )
        )
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            update(conn, "obligation", ids["obligation"], "SETTLED")
        update(conn, "obligation", ids["obligation"], "SCHEDULED")
        assert state(conn, "obligation", ids["obligation"]) == "SCHEDULED"


def test_downgrade_removes_guards_and_upgrade_restores_them(db_schema):
    db_schema.migrate("0003", downgrade=True)
    with db_schema.connect() as conn:
        assert (
            conn.execute("SELECT to_regclass('allowed_transitions') AS name").fetchone()["name"]
            is None
        )
        assert (
            conn.execute("SELECT to_regprocedure('check_allowed_transition()') AS name").fetchone()[
                "name"
            ]
            is None
        )
        ids = seed(conn)
        update(conn, "obligation", ids["obligation"], "SETTLED")
    db_schema.migrate()
    with (
        db_schema.connect() as conn,
        pytest.raises(psycopg.errors.CheckViolation),
        conn.transaction(),
    ):
        update(conn, "obligation", ids["obligation"], "IN_FLIGHT")
