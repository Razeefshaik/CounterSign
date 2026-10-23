"""Version every row a state machine moves, and fence proposal and incident states.

PLAN 2.2 left out the version column on operations, payout_attempts and proposals, and
the state CHECK on proposals and incidents. Those are the rows where a lost race would
hurt most (a PayPal write, a payout attempt, an owner-approved proposal), so they get
the same compare-and-set guard as obligations. The CHECK lists equal the C10 enums; a
unit test keeps them from drifting.
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

PROPOSAL_STATES = (
    "CREATED", "ALLOWED", "BLOCKED", "NEEDS_OWNER", "APPROVED", "REJECTED", "EXPIRED",
    "EXECUTING", "EXECUTED", "BLOCKED_AT_EXECUTION", "FAILED",
)
INCIDENT_STATES = (
    "OPEN", "INVESTIGATING", "AWAITING_OWNER", "EXECUTING", "WAITING_EXTERNAL", "RESOLVED",
)
VERSIONED = ("operations", "payout_attempts", "proposals")


def in_list(values) -> str:
    return ",".join(f"'{value}'" for value in values)


def upgrade() -> None:
    for table in VERSIONED:
        op.execute(f"ALTER TABLE {table} ADD COLUMN version int NOT NULL DEFAULT 1")
    op.execute(
        "ALTER TABLE proposals ADD CONSTRAINT proposals_state_check "
        f"CHECK (state IN ({in_list(PROPOSAL_STATES)}))"
    )
    op.execute(
        "ALTER TABLE incidents ADD CONSTRAINT incidents_state_check "
        f"CHECK (state IN ({in_list(INCIDENT_STATES)}))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE incidents DROP CONSTRAINT incidents_state_check")
    op.execute("ALTER TABLE proposals DROP CONSTRAINT proposals_state_check")
    for table in reversed(VERSIONED):
        op.execute(f"ALTER TABLE {table} DROP COLUMN version")
