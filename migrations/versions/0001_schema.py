"""Create the PLAN 2.2 slice schema and deferred journal balance constraint.

State-transition triggers and allowed_transitions are intentionally deferred.
Postings require a journal: nullable journal_id would bypass journal balancing.
"""

from pathlib import Path

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

SQL_ROOT = Path(__file__).resolve().parents[1] / "sql"


def upgrade() -> None:
    op.execute((SQL_ROOT / "0001_schema.sql").read_text(encoding="utf-8"))
    op.execute((SQL_ROOT / "0001_ledger_balance.sql").read_text(encoding="utf-8"))


def downgrade() -> None:
    op.execute("DROP TABLE ledger_postings")
    op.execute("DROP FUNCTION check_ledger_journal_balance()")
    op.execute("DROP FUNCTION lock_ledger_journal()")
    for table in (
        "audit_log", "policies", "balance_snapshots", "webhook_inbox", "approvals",
        "proposals", "incidents", "ledger_journals", "reservations", "disputes",
        "payout_attempts", "operations", "payout_line_obligations", "payout_lines",
        "payout_runs", "obligations", "orders", "makers",
    ):
        op.execute(f"DROP TABLE {table}")
