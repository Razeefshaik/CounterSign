"""C31 allows cancelled history without weakening one live run per week."""

from datetime import date
from uuid import uuid4

import psycopg
import pytest
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.integration
WEEK = date(2026, 11, 2)


def insert(conn, state):
    identifier = uuid4()
    conn.execute("INSERT INTO payout_runs(id,week_start,state) VALUES (%s,%s,%s)",
                 (identifier, WEEK, state))
    return identifier


@pytest.mark.parametrize("state", ["DRAFT", "AWAITING_OWNER", "APPROVED", "EXECUTING", "DONE"])
def test_partial_index_enforces_one_non_cancelled_run(db_schema, state):
    with db_schema.connect() as conn:
        insert(conn, "CANCELLED")
        live = insert(conn, state)
        insert(conn, "CANCELLED")
        with pytest.raises(psycopg.errors.UniqueViolation):
            insert(conn, "AWAITING_OWNER")
        docs = conn.execute("SELECT doc FROM v_dash_runs").fetchall()
        assert [doc["doc"]["id"] for doc in docs] == [str(live)]


def test_migration_roundtrip_restores_constraint_and_never_discards_history(schema_factory):
    schema = schema_factory("0005")
    schema.migrate()
    with schema.connect() as conn:
        old = insert(conn, "CANCELLED")
        live = insert(conn, "AWAITING_OWNER")
    # Downgrading a week with replacement history must refuse, never delete rows.
    with pytest.raises(IntegrityError):
        schema.migrate("0005", downgrade=True)
    with schema.connect() as conn:
        assert conn.execute("SELECT count(*) AS n FROM payout_runs").fetchone()["n"] == 2
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone()["version_num"] == "0006"
        conn.execute("DELETE FROM payout_runs WHERE id=%s", (live,))
    schema.migrate("0005", downgrade=True)
    with schema.connect() as conn:
        with pytest.raises(psycopg.errors.UniqueViolation):
            insert(conn, "AWAITING_OWNER")
        [saved] = conn.execute("SELECT id FROM payout_runs").fetchall()
        assert saved["id"] == old
    schema.migrate()
    with schema.connect() as conn:
        insert(conn, "AWAITING_OWNER")
