"""Guard application-owned state transitions, retaining all GET-confirmed mirrors."""

import json
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

SNAPSHOT = Path(__file__).resolve().parents[1] / "sql" / "0004_allowed_transitions.json"
TABLES = (
    ("obligations", "obligation"),
    ("payout_lines", "line"),
    ("payout_attempts", "attempt"),
    ("operations", "operation"),
    ("proposals", "proposal"),
    ("incidents", "incident"),
)


def upgrade():
    table = op.create_table(
        "allowed_transitions",
        sa.Column("entity", sa.Text(), nullable=False),
        sa.Column("from_state", sa.Text(), nullable=False),
        sa.Column("to_state", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("entity", "from_state", "to_state"),
    )
    rows = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    op.bulk_insert(
        table, [dict(zip(("entity", "from_state", "to_state"), row, strict=True)) for row in rows]
    )
    op.execute("""
CREATE FUNCTION check_allowed_transition() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE permitted boolean;
BEGIN
    IF OLD.state = NEW.state THEN
        RETURN NEW;
    END IF;
    -- PREPARED and UNKNOWN are ours. Every subsequent GET mirror must be kept,
    -- even a surprising transition; reconciliation opens the incident separately.
    IF TG_TABLE_NAME = 'payout_attempts' AND OLD.state NOT IN ('PREPARED','UNKNOWN') THEN
        RETURN NEW;
    END IF;
    -- Resolve the policy table in the updated row's schema, not caller search_path.
    EXECUTE format('SELECT EXISTS (SELECT 1 FROM %I.allowed_transitions '
                   'WHERE entity=$1 AND from_state=$2 AND to_state=$3)', TG_TABLE_SCHEMA)
        INTO permitted USING TG_ARGV[0], OLD.state, NEW.state;
    IF NOT permitted THEN
        RAISE EXCEPTION 'illegal % state transition: % -> %', TG_ARGV[0], OLD.state, NEW.state
            USING ERRCODE='23514', CONSTRAINT='allowed_transitions',
                  TABLE=TG_TABLE_NAME, SCHEMA=TG_TABLE_SCHEMA;
    END IF;
    RETURN NEW;
END;
$$;
""")
    for table_name, entity in TABLES:
        # Fixed migration identifiers and arguments, never application input.
        op.execute(
            f"CREATE TRIGGER guard_state_transition BEFORE UPDATE OF state ON {table_name} "
            f"FOR EACH ROW EXECUTE FUNCTION check_allowed_transition('{entity}')"
        )


def downgrade():
    for table_name, _ in reversed(TABLES):
        op.execute(f"DROP TRIGGER guard_state_transition ON {table_name}")
    op.execute("DROP FUNCTION check_allowed_transition()")
    op.drop_table("allowed_transitions")
