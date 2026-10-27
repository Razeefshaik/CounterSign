"""C31: a cancelled run does not prevent a fresh request for the same week."""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

# Freeze both definitions: historical migrations must not import application code.
ORIGINAL_RUNS = r"""
CREATE OR REPLACE VIEW v_dash_runs AS
SELECT r.id,r.state,r.week_start,
 jsonb_build_object('id',r.id,'week_start',r.week_start::text,'state',r.state,
  'total',dash_money(coalesce(t.total,0)+coalesce((SELECT sum(old.amount_minor)::bigint
     FROM jsonb_array_elements(coalesce(p.params->'replacements','[]')) replacement
     JOIN payout_lines old ON old.id::text=replacement->>'line_id'
     WHERE old.run_id<>r.id AND old.currency='USD'),0),coalesce(t.currency,'USD')),
  'line_count',coalesce(t.line_count,0),
  'replacement_count',coalesce(jsonb_array_length(p.params->'replacements'),0),
  'reserved_not_paid',dash_money(coalesce((SELECT sum(o.amount_minor)::bigint
    FROM obligations o WHERE o.state='RESERVED' AND o.currency=coalesce(t.currency,'USD')
    AND o.due_date>=r.week_start AND o.due_date<r.week_start+7),0),coalesce(t.currency,'USD')),
  'approval',(SELECT jsonb_build_object('id',a.id,'approved_by',a.approved_by,
    'approved_at',dash_time(a.approved_at),'expires_at',dash_time(a.expires_at),
    'payload_hash','sha256:'||encode(a.payload_hash,'hex'),'used_at',dash_time(a.used_at))
    FROM approvals a WHERE a.id=r.approval_id),
  'created_at',dash_time(p.created_at)) AS doc
FROM payout_runs r
LEFT JOIN LATERAL (SELECT sum(l.amount_minor)::bigint total,min(l.currency::text) currency,
 count(*)::int line_count FROM payout_lines l WHERE l.run_id=r.id
 HAVING count(DISTINCT l.currency)<=1) t ON true
LEFT JOIN LATERAL (SELECT * FROM proposals p WHERE p.params->>'run_id'=r.id::text
 AND p.action='SCHEDULE_PAYOUT_RUN' ORDER BY p.created_at DESC,p.id DESC LIMIT 1) p ON true
WHERE NOT EXISTS (SELECT 1 FROM payout_lines other WHERE other.run_id=r.id
 AND other.currency<>'USD') AND NOT EXISTS (
 SELECT 1 FROM jsonb_array_elements(coalesce(p.params->'replacements','[]')) replacement
 JOIN payout_lines old ON old.id::text=replacement->>'line_id' WHERE old.currency<>'USD');
"""

LIVE_RUNS = r"""
CREATE OR REPLACE VIEW v_dash_runs AS
SELECT r.id,r.state,r.week_start,
 jsonb_build_object('id',r.id,'week_start',r.week_start::text,'state',r.state,
  'total',dash_money(coalesce(t.total,0)+coalesce((SELECT sum(old.amount_minor)::bigint
     FROM jsonb_array_elements(coalesce(p.params->'replacements','[]')) replacement
     JOIN payout_lines old ON old.id::text=replacement->>'line_id'
     WHERE old.run_id<>r.id AND old.currency='USD'),0),coalesce(t.currency,'USD')),
  'line_count',coalesce(t.line_count,0),
  'replacement_count',coalesce(jsonb_array_length(p.params->'replacements'),0),
  'reserved_not_paid',dash_money(coalesce((SELECT sum(o.amount_minor)::bigint
    FROM obligations o WHERE o.state='RESERVED' AND o.currency=coalesce(t.currency,'USD')
    AND o.due_date>=r.week_start AND o.due_date<r.week_start+7),0),coalesce(t.currency,'USD')),
  'approval',(SELECT jsonb_build_object('id',a.id,'approved_by',a.approved_by,
    'approved_at',dash_time(a.approved_at),'expires_at',dash_time(a.expires_at),
    'payload_hash','sha256:'||encode(a.payload_hash,'hex'),'used_at',dash_time(a.used_at))
    FROM approvals a WHERE a.id=r.approval_id),
  'created_at',dash_time(p.created_at)) AS doc
FROM payout_runs r
LEFT JOIN LATERAL (SELECT sum(l.amount_minor)::bigint total,min(l.currency::text) currency,
 count(*)::int line_count FROM payout_lines l WHERE l.run_id=r.id
 HAVING count(DISTINCT l.currency)<=1) t ON true
LEFT JOIN LATERAL (SELECT * FROM proposals p WHERE p.params->>'run_id'=r.id::text
 AND p.action='SCHEDULE_PAYOUT_RUN' ORDER BY p.created_at DESC,p.id DESC LIMIT 1) p ON true
WHERE NOT EXISTS (SELECT 1 FROM payout_lines other WHERE other.run_id=r.id
 AND other.currency<>'USD') AND NOT EXISTS (
 SELECT 1 FROM jsonb_array_elements(coalesce(p.params->'replacements','[]')) replacement
 JOIN payout_lines old ON old.id::text=replacement->>'line_id' WHERE old.currency<>'USD')
 AND (r.state<>'CANCELLED' OR NOT EXISTS (
 SELECT 1 FROM payout_runs live WHERE live.week_start=r.week_start AND live.state<>'CANCELLED'));
"""


def upgrade():
    op.execute("ALTER TABLE payout_runs DROP CONSTRAINT payout_runs_week_start_key")
    op.execute("CREATE UNIQUE INDEX uq_one_live_run_per_week ON payout_runs(week_start) "
               "WHERE state <> 'CANCELLED'")
    # CREATE OR REPLACE preserves grants and the dependent detail view. Cancelled
    # history is visible only when the week has no live replacement.
    op.execute(LIVE_RUNS)


def downgrade():
    # Refuse incompatible duplicate history rather than deleting financial data.
    op.execute("ALTER TABLE payout_runs ADD CONSTRAINT payout_runs_week_start_key "
               "UNIQUE (week_start)")
    op.execute("DROP INDEX uq_one_live_run_per_week")
    op.execute(ORIGINAL_RUNS)
