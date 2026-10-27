"""M17: informative incident titles built only from fixed record fields.

Titles name the PayPal dispute ID, the maker's display name, the amount, the
payout attempt state, or a fixed goal/reason code in plain words. They never
read buyer messages, model output or a subject-supplied title. The view keeps
its columns, so v_dash_incident_detail and the read-only grant stay unchanged.
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

# Lookups are inline subqueries: a view reads its tables with the owner's rights,
# so the read-only dashboard role still never reads disputes, makers or attempts.
TITLE = r"""coalesce(nullif(CASE i.kind
 WHEN 'DISPUTE' THEN (SELECT concat_ws(' · ',
     'Dispute ' || coalesce(d.paypal_dispute_id, i.subject->>'paypal_dispute_id'),
     (SELECT m.display_name FROM orders o JOIN makers m ON m.id=o.maker_id
      WHERE o.id::text=coalesce(d.order_id::text, i.subject->>'order_id')),
     dash_amount_text(d.amount_minor, d.currency))
   FROM (SELECT 1) one
   LEFT JOIN disputes d ON d.id::text=i.subject->>'dispute_id')
 WHEN 'PAYOUT_ITEM' THEN (SELECT concat_ws(' · ',
     CASE a.state WHEN 'UNCLAIMED' THEN 'Unclaimed payout' WHEN 'RETURNED' THEN 'Returned payout'
       WHEN 'UNKNOWN' THEN 'Payout outcome unknown' WHEN 'ONHOLD' THEN 'Payout on hold'
       WHEN 'BLOCKED' THEN 'Blocked payout' WHEN 'FAILED' THEN 'Failed payout'
       WHEN 'REFUNDED' THEN 'Refunded payout' WHEN 'REVERSED' THEN 'Reversed payout'
       WHEN 'SUCCESS' THEN 'Payout paid' ELSE 'Payout item' END,
     (SELECT m.display_name FROM makers m
      WHERE m.id::text=coalesce(l.maker_id::text, i.subject->>'maker_id')),
     coalesce(dash_amount_text(a.amount_minor, a.currency),
       dash_amount_text((i.subject#>>'{amount_at_risk,minor}')::bigint,
         i.subject#>>'{amount_at_risk,currency}')))
   FROM (SELECT 1) one
   LEFT JOIN payout_attempts a ON a.id::text=i.subject->>'attempt_id'
   LEFT JOIN payout_lines l ON l.id=a.line_id)
 WHEN 'RUN_AT_RISK' THEN concat_ws(' · ', 'Payout run at risk',
   (SELECT 'week of ' || r.week_start::text FROM payout_runs r
    WHERE r.id::text=i.subject->>'run_id'))
 ELSE concat_ws(' · ',
   CASE WHEN i.subject->>'reason' LIKE 'DEMO\_%' THEN 'Staged goal: ' ||
     CASE WHEN i.subject->>'reason' LIKE 'DEMO\_EVIDENCE%' THEN 'provide dispute evidence'
       WHEN i.subject->>'reason' LIKE 'DEMO\_CANCEL%' THEN 'cancel the unclaimed payout'
       WHEN i.subject->>'reason' LIKE 'DEMO\_MAKER\_A\_NOTE%' THEN 'draft a note to Maker A'
       WHEN i.subject->>'reason' LIKE 'DEMO\_OWNER\_CLAIM%' THEN 'accept the buyer claim (owner decides)'
       WHEN i.subject->>'reason' LIKE 'DEMO\_INJECTION%' THEN 'refund to a different account'
       WHEN i.subject->>'reason' LIKE 'DEMO\_SETTLED\_RETRY%' THEN 'repay an already paid line'
       ELSE lower(dash_code_text(regexp_replace(i.subject->>'reason', '^DEMO_', ''))) END
   ELSE dash_code_text(i.subject->>'reason') END,
   CASE WHEN i.subject->>'entity'='dispute' THEN (SELECT 'Dispute ' || d.paypal_dispute_id
       FROM disputes d WHERE d.id::text=i.subject->>'id')
     WHEN i.subject->>'entity' IN ('attempt','payout_attempt') THEN 'Payout attempt'
     WHEN i.subject ? 'entity' THEN dash_code_text(upper(i.subject->>'entity')) END,
   CASE WHEN i.subject ? 'paypal_dispute_id' AND i.subject->>'entity' IS DISTINCT FROM 'dispute'
     THEN 'Dispute ' || (i.subject->>'paypal_dispute_id') END)
END, ''), CASE i.kind WHEN 'DISPUTE' THEN 'Dispute' WHEN 'PAYOUT_ITEM' THEN 'Payout item'
  WHEN 'RUN_AT_RISK' THEN 'Payout run at risk' ELSE 'Reconciliation' END)"""


def _view(title):
    return rf"""
CREATE OR REPLACE VIEW v_dash_incidents AS
SELECT i.id, i.state, i.kind,
  greatest(i.opened_at, coalesce(i.closed_at, i.opened_at),
    coalesce((SELECT max(p.created_at) FROM proposals p WHERE p.incident_id=i.id),
      i.opened_at), coalesce((SELECT max(a.at) FROM audit_log a
        WHERE a.details->>'incident_id'=i.id::text), i.opened_at)) AS updated_at,
  dash_redact(jsonb_build_object('id', i.id, 'kind', i.kind, 'state', i.state,
    'title', {title},
    'subject', jsonb_build_object('dispute_id', i.subject->'dispute_id',
       'paypal_dispute_id', i.subject->'paypal_dispute_id', 'order_id', i.subject->'order_id',
       'maker_id', i.subject->'maker_id', 'payout_item_id', i.subject->'payout_item_id',
       'run_id', i.subject->'run_id'),
    'amount_at_risk', coalesce(i.subject->'amount_at_risk',
       (SELECT dash_money(d.amount_minor,d.currency) FROM disputes d
        WHERE d.id::text=i.subject->>'dispute_id')),
    'deadline', coalesce(i.subject->>'deadline',
       (SELECT dash_time(d.seller_response_due_date) FROM disputes d
        WHERE d.id::text=i.subject->>'dispute_id')),
    'latest_proposal', (SELECT jsonb_build_object('id',p.id, 'action',p.action,
       'verdict',p.verdict,'state',p.state) FROM proposals p WHERE p.incident_id=i.id
       ORDER BY p.created_at DESC,p.id DESC LIMIT 1),
    'opened_at',dash_time(i.opened_at),
    'updated_at',dash_time(greatest(i.opened_at,coalesce(i.closed_at,i.opened_at),
       coalesce((SELECT max(p.created_at) FROM proposals p WHERE p.incident_id=i.id),
         i.opened_at),coalesce((SELECT max(a.at) FROM audit_log a
         WHERE a.details->>'incident_id'=i.id::text),i.opened_at))),
    'closed_at',dash_time(i.closed_at))) AS doc
FROM incidents i;
"""


# 0002's original title: a subject title, else the kind name.
ORIGINAL_TITLE = r"""coalesce(i.subject->>'title',
       CASE i.kind WHEN 'DISPUTE' THEN 'Dispute' WHEN 'PAYOUT_ITEM' THEN 'Payout item'
       WHEN 'RUN_AT_RISK' THEN 'Payout run at risk' ELSE 'Reconciliation' END)"""


def upgrade():
    op.execute(r"""
CREATE FUNCTION dash_amount_text(value bigint, currency text) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE
RETURN CASE WHEN value IS NULL OR currency IS NULL THEN NULL
  ELSE (CASE WHEN value < 0 THEN '-' ELSE '' END)
    || (CASE WHEN btrim(currency) = 'USD' THEN '$' ELSE '' END)
    || to_char(abs(value) / 100, 'FM999,999,999,999,990') || '.'
    || lpad((abs(value) % 100)::text, 2, '0')
    || (CASE WHEN btrim(currency) = 'USD' THEN '' ELSE ' ' || btrim(currency) END) END;

CREATE FUNCTION dash_code_text(value text) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE
RETURN CASE WHEN value ~ '^[A-Z][A-Z0-9_]{0,79}$'
  THEN upper(left(value, 1)) || lower(replace(substr(value, 2), '_', ' ')) END;
""")
    op.execute(_view(TITLE))


def downgrade():
    op.execute(_view(ORIGINAL_TITLE))
    op.execute("DROP FUNCTION dash_code_text(text), dash_amount_text(bigint,text);")
