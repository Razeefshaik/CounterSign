"""Contract-shaped read-only dashboard documents.

No schema guesses: optional presentation metadata lives in existing JSON fields.
The database role cannot read the underlying PayPal payloads or payout emails.
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(r"""
CREATE FUNCTION dash_time(value timestamptz) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE
RETURN to_char(value AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"');

CREATE FUNCTION dash_money(value bigint, currency text) RETURNS jsonb
LANGUAGE sql IMMUTABLE PARALLEL SAFE
RETURN CASE WHEN value IS NULL OR currency IS NULL THEN NULL
       ELSE jsonb_build_object('minor', value, 'currency', currency) END;

CREATE FUNCTION dash_redact(value jsonb) RETURNS jsonb
LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE AS $function$
DECLARE result jsonb; item record;
BEGIN
  IF jsonb_typeof(value) = 'object' THEN
    result := '{}';
    FOR item IN SELECT * FROM jsonb_each(value) LOOP
      IF item.key !~* '(authorization|token|secret|password|client_id)' THEN
        result := result || jsonb_build_object(item.key, dash_redact(item.value));
      END IF;
    END LOOP;
    RETURN result;
  ELSIF jsonb_typeof(value) = 'array' THEN
    SELECT coalesce(jsonb_agg(dash_redact(element)), '[]') INTO result
    FROM jsonb_array_elements(value) element;
    RETURN result;
  ELSIF jsonb_typeof(value) = 'string' THEN
    RETURN to_jsonb(regexp_replace(value #>> '{}',
      '([[:alnum:]])[[:alnum:]._%+\-]*@([[:alnum:].\-]+\.[[:alpha:]]{2,})',
      '\1***@\2', 'g'));
  END IF;
  RETURN value;
END;
$function$;

CREATE VIEW v_dash_audit AS
SELECT a.id, a.at, a.proposal_id, a.operation_id, a.actor, a.kind,
  jsonb_build_object('id', a.id, 'at', dash_time(a.at), 'actor', a.actor,
    'kind', a.kind, 'proposal_id', a.proposal_id, 'operation_id', a.operation_id,
    'rule', a.rule, 'verdict', a.verdict, 'details', dash_redact(a.details)) AS doc
FROM audit_log a;

CREATE VIEW v_dash_proposals AS
SELECT p.id, p.incident_id, p.state, p.verdict, p.action, p.created_at,
  dash_redact(jsonb_build_object(
    'id', p.id, 'incident_id', p.incident_id, 'action', p.action,
    'params', p.params, 'evidence_refs', p.evidence_refs, 'rationale', p.rationale,
    'expected_effect', p.expected_effect,
    'tier', CASE WHEN p.action IN ('SCHEDULE_PAYOUT_RUN','CREATE_REPLACEMENT_PAYOUT',
       'DEFER_SCHEDULED_PAYOUT','ACCEPT_CLAIM') THEN 'OWNER' ELSE 'AUTONOMOUS' END,
    'verdict', p.verdict,
    'rule_results', coalesce(p.verdict_details->'rule_results',
       CASE WHEN jsonb_typeof(p.verdict_details) = 'array' THEN p.verdict_details END, '[]'),
    'flags', coalesce(p.raw_output->'flags', '[]'), 'state', p.state, 'model', p.model,
    'approval', (SELECT jsonb_build_object('id', a.id, 'approved_by', a.approved_by,
       'approved_at', dash_time(a.approved_at), 'expires_at', dash_time(a.expires_at),
       'payload_hash', 'sha256:' || encode(a.payload_hash, 'hex'),
       'used_at', dash_time(a.used_at)) FROM approvals a WHERE a.proposal_id = p.id
       ORDER BY a.approved_at DESC, a.id DESC LIMIT 1),
    'operation', (SELECT jsonb_build_object('id', o.id, 'kind', o.kind, 'state', o.state,
       'paypal_refs', jsonb_strip_nulls(jsonb_build_object(
         'payout_batch_id', coalesce(o.response->>'payout_batch_id',
             o.response#>>'{batch_header,payout_batch_id}'),
         'payout_item_id', o.response->>'payout_item_id', 'debug_id', o.paypal_debug_id)))
       FROM operations o WHERE o.proposal_id = p.id
       ORDER BY o.created_at DESC, o.id DESC LIMIT 1),
    'created_at', dash_time(p.created_at))
    || CASE WHEN p.state = 'NEEDS_OWNER' AND p.raw_output ? 'approval_request'
       THEN jsonb_build_object('approval_request', p.raw_output->'approval_request')
       ELSE '{}'::jsonb END) AS doc
FROM proposals p;

CREATE VIEW v_dash_incidents AS
SELECT i.id, i.state, i.kind,
  greatest(i.opened_at, coalesce(i.closed_at, i.opened_at),
    coalesce((SELECT max(p.created_at) FROM proposals p WHERE p.incident_id=i.id),
      i.opened_at), coalesce((SELECT max(a.at) FROM audit_log a
        WHERE a.details->>'incident_id'=i.id::text), i.opened_at)) AS updated_at,
  dash_redact(jsonb_build_object('id', i.id, 'kind', i.kind, 'state', i.state,
    'title', coalesce(i.subject->>'title',
       CASE i.kind WHEN 'DISPUTE' THEN 'Dispute' WHEN 'PAYOUT_ITEM' THEN 'Payout item'
       WHEN 'RUN_AT_RISK' THEN 'Payout run at risk' ELSE 'Reconciliation' END),
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

CREATE VIEW v_dash_incident_detail AS
SELECT i.id, i.doc || jsonb_build_object(
  'proposals',coalesce((SELECT jsonb_agg(p.doc ORDER BY p.created_at DESC,p.id DESC)
     FROM v_dash_proposals p WHERE p.incident_id=i.id),'[]'),
  'timeline',coalesce((SELECT jsonb_agg(a.doc ORDER BY a.at,a.id) FROM v_dash_audit a
     WHERE a.proposal_id IN (SELECT p.id FROM proposals p WHERE p.incident_id=i.id)
       OR EXISTS (SELECT 1 FROM audit_log original WHERE original.id=a.id
         AND original.details->>'incident_id'=i.id::text)),'[]')) AS doc
FROM v_dash_incidents i;

CREATE VIEW v_dash_reservations AS
SELECT r.id,r.state,r.kind,r.dispute_id,r.run_id,r.created_at,
  dash_redact(jsonb_build_object('id',r.id,'kind',r.kind,'affects_cash',r.affects_cash,
    'state',r.state,'amount',dash_money(r.amount_minor,r.currency),
    'obligation_id',r.obligation_id,'dispute_id',r.dispute_id,'proposal_id',r.proposal_id,
    'run_id',r.run_id,'reason',CASE r.kind WHEN 'DISPUTE_EARMARK' THEN 'Dispute earmark'
      WHEN 'RUN_CASH' THEN 'Approved payout run' ELSE 'Pending proposal' END,
    'created_at',dash_time(r.created_at),'closed_at',dash_time(r.closed_at))) AS doc
FROM reservations r;

CREATE VIEW v_dash_makers AS
SELECT m.id,m.display_name,
 dash_redact(jsonb_build_object('id',m.id,'display_name',m.display_name,
  'payout_email_masked',m.payout_email,
  'payout_email_verified',m.payout_email_verified_at IS NOT NULL,
  'payout_email_verified_at',dash_time(m.payout_email_verified_at),
  'owed_open',dash_money(coalesce(t.open,0),coalesce(t.currency,'USD')),
  'reserved',dash_money(coalesce(t.reserved,0),coalesce(t.currency,'USD')),
  'scheduled',dash_money(coalesce(t.scheduled,0),coalesce(t.currency,'USD')),
  'in_flight',dash_money(coalesce(t.in_flight,0),coalesce(t.currency,'USD')),
  'settled_last_30d',dash_money(coalesce(t.settled,0),coalesce(t.currency,'USD')))) AS doc
FROM makers m
LEFT JOIN LATERAL (SELECT min(o.currency::text) currency,
 sum(o.amount_minor) FILTER(WHERE o.state='OPEN')::bigint open,
 sum(o.amount_minor) FILTER(WHERE o.state='RESERVED')::bigint reserved,
 sum(o.amount_minor) FILTER(WHERE o.state='SCHEDULED')::bigint scheduled,
 sum(o.amount_minor) FILTER(WHERE o.state='IN_FLIGHT')::bigint in_flight,
 sum(o.amount_minor) FILTER(WHERE o.state='SETTLED'
   AND o.updated_at>=now()-interval '30 days')::bigint settled
 FROM obligations o WHERE o.maker_id=m.id AND o.currency='USD'
 HAVING count(DISTINCT o.currency)<=1) t ON true;

CREATE VIEW v_dash_disputes AS
SELECT d.id,d.phase,d.status,coalesce(d.last_synced_at,'epoch'::timestamptz) updated_at,
 dash_redact(jsonb_build_object('id',d.id,'paypal_dispute_id',d.paypal_dispute_id,
  'order',(SELECT jsonb_build_object('id',o.id,
     'ref',coalesce(o.tracking->>'order_ref',o.paypal_order_id,o.id::text),
     'maker_id',o.maker_id,'amount',dash_money(o.amount_minor,o.currency))
     FROM orders o WHERE o.id=d.order_id),
  'reason',d.reason,'status',d.status,'lifecycle_stage',d.lifecycle_stage,'phase',d.phase,
  'amount',dash_money(d.amount_minor,d.currency),
  'seller_response_due_date',dash_time(d.seller_response_due_date),
  'outcome_code',d.outcome_code,'amount_refunded',dash_money(d.amount_refunded_minor,d.currency),
  'hold_observed',CASE WHEN jsonb_typeof(d.raw->'hold_observed')='boolean'
    THEN d.raw->'hold_observed' ELSE 'null'::jsonb END,
  'reserved',dash_money(coalesce((SELECT sum(r.amount_minor)::bigint FROM reservations r
    WHERE r.dispute_id=d.id AND r.kind='DISPUTE_EARMARK' AND r.state='ACTIVE'
    AND r.currency=d.currency),0),d.currency),
  'available_actions',coalesce((SELECT jsonb_agg(link->>'rel')
    FROM jsonb_array_elements(coalesce(d.links,'[]')) link WHERE link ? 'rel'),'[]'),
  'staged',CASE WHEN jsonb_typeof(d.raw->'staged')='boolean'
    THEN d.raw->'staged' ELSE 'false'::jsonb END,
  'last_synced_at',dash_time(d.last_synced_at),'updated_at',dash_time(d.last_synced_at))) AS doc
FROM disputes d;

CREATE VIEW v_dash_runs AS
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

CREATE VIEW v_dash_run_detail AS
SELECT r.id,r.doc||jsonb_build_object(
 'lines',coalesce((SELECT jsonb_agg(jsonb_build_object('id',l.id,
   'maker',jsonb_build_object('id',m.id,'display_name',m.display_name),
   'amount',dash_money(l.amount_minor,l.currency),'state',l.state,
   'obligations',coalesce((SELECT jsonb_agg(jsonb_build_object('id',o.id,
     'order_ref',coalesce(ord.tracking->>'order_ref',ord.paypal_order_id,ord.id::text),
     'kind',o.kind,'amount',dash_money(lo.amount_minor,o.currency),'state',o.state,
     'dispute_id',(SELECT d.id FROM disputes d WHERE d.order_id=o.order_id
        AND d.phase='ACTIVE' ORDER BY d.id LIMIT 1)) ORDER BY o.id)
     FROM payout_line_obligations lo JOIN obligations o ON o.id=lo.obligation_id
     LEFT JOIN orders ord ON ord.id=o.order_id WHERE lo.line_id=l.id AND lo.active),'[]'),
   'attempts',coalesce((SELECT jsonb_agg(dash_redact(jsonb_build_object('id',a.id,
     'attempt_no',a.attempt_no,'state',a.state,'sender_batch_id',a.sender_batch_id,
     'request_id',a.request_id,'paypal_batch_id',a.paypal_batch_id,
     'paypal_item_id',a.paypal_item_id,'payee_email_masked',a.payee_email,
     'amount',dash_money(a.amount_minor,a.currency),'prepared_at',dash_time(a.prepared_at),
     'last_checked_at',dash_time(a.last_checked_at))) ORDER BY a.attempt_no)
     FROM payout_attempts a WHERE a.line_id=l.id),'[]')) ORDER BY l.id)
   FROM payout_lines l JOIN makers m ON m.id=l.maker_id WHERE l.run_id=r.id),'[]'),
 'replacements',coalesce((SELECT jsonb_agg(jsonb_build_object('line_id',l.id,
    'from_run_id',l.run_id,'attempt_no',(replacement->>'attempt_no')::int,
    'maker',jsonb_build_object('id',m.id,'display_name',m.display_name),
    'amount',dash_money(l.amount_minor,l.currency),
    'previous_attempt_state',(SELECT a.state FROM payout_attempts a WHERE a.line_id=l.id
      AND a.attempt_no<(replacement->>'attempt_no')::int ORDER BY a.attempt_no DESC LIMIT 1)))
   FROM proposals p CROSS JOIN LATERAL jsonb_array_elements(
      coalesce(p.params->'replacements','[]')) replacement
   JOIN payout_lines l ON l.id::text=replacement->>'line_id'
   JOIN makers m ON m.id=l.maker_id
   WHERE p.id=(SELECT latest.id FROM proposals latest
     WHERE latest.action='SCHEDULE_PAYOUT_RUN' AND latest.params->>'run_id'=r.id::text
     ORDER BY latest.created_at DESC,latest.id DESC LIMIT 1)),'[]'),
 'reserved',coalesce((SELECT jsonb_agg(jsonb_build_object(
    'maker',jsonb_build_object('id',m.id,'display_name',m.display_name),
    'obligation_id',o.id,'amount',dash_money(o.amount_minor,o.currency),'reason','DISPUTE',
    'dispute_id',(SELECT d.id FROM disputes d WHERE d.order_id=o.order_id
      AND d.phase='ACTIVE' ORDER BY d.id LIMIT 1)) ORDER BY o.id)
   FROM obligations o JOIN makers m ON m.id=o.maker_id WHERE o.state='RESERVED'
   AND o.currency='USD' AND o.due_date>=r.week_start
   AND o.due_date<r.week_start+7),'[]')) AS doc
FROM v_dash_runs r;

CREATE VIEW v_dash_cash AS
WITH snapshot AS (
 SELECT * FROM balance_snapshots WHERE currency='USD' AND available_minor IS NOT NULL
  AND withheld_minor IS NOT NULL AND total_minor IS NOT NULL
  AND last_refresh_time IS NOT NULL AND as_of_time IS NOT NULL
  ORDER BY fetched_at DESC,id DESC LIMIT 1
), outflows AS (
 SELECT o.id,o.kind,o.submitted_at,
 CASE WHEN o.kind='PAYOUT_CREATE' THEN (SELECT a.amount_minor FROM payout_attempts a
    WHERE a.operation_id=o.id AND a.currency=s.currency LIMIT 1)
 WHEN o.kind='DISPUTE_ACCEPT_CLAIM' THEN (SELECT d.amount_minor FROM disputes d
    WHERE d.id=o.target_id AND d.currency=s.currency) END amount_minor
 FROM operations o CROSS JOIN snapshot s WHERE o.submitted_at>s.last_refresh_time
 AND o.state IN ('IN_FLIGHT','SUCCEEDED','UNKNOWN')
 AND o.kind IN ('PAYOUT_CREATE','DISPUTE_ACCEPT_CLAIM')
), totals AS (
 SELECT s.*,
  coalesce((SELECT sum(amount_minor)::bigint FROM outflows),0) outflow,
  coalesce((SELECT sum(r.amount_minor)::bigint FROM reservations r WHERE r.state='ACTIVE'
    AND r.affects_cash AND r.currency=s.currency),0) cash,
  coalesce((SELECT sum(r.amount_minor)::bigint FROM reservations r WHERE r.state='ACTIVE'
    AND r.kind='RUN_CASH' AND r.currency=s.currency),0) run_cash,
  coalesce((SELECT sum(r.amount_minor)::bigint FROM reservations r WHERE r.state='ACTIVE'
    AND r.kind='PROPOSAL_CASH' AND r.currency=s.currency),0) proposal_cash,
  coalesce((SELECT sum(r.amount_minor)::bigint FROM reservations r WHERE r.state='ACTIVE'
    AND r.kind='DISPUTE_EARMARK' AND r.currency=s.currency),0) earmark,
  coalesce((SELECT sum(a.amount_minor)::bigint FROM payout_attempts a
    WHERE a.state='UNCLAIMED' AND a.currency=s.currency),0) unclaimed
 FROM snapshot s
)
SELECT currency,jsonb_build_object('currency',currency,
 'paypal',jsonb_build_object('available',dash_money(available_minor,currency),
   'withheld',dash_money(withheld_minor,currency),'total',dash_money(total_minor,currency),
   'last_refresh_time',dash_time(last_refresh_time),'as_of_time',dash_time(as_of_time),
   'fetched_at',dash_time(fetched_at),
   'age_seconds',greatest(0,floor(extract(epoch FROM now()-last_refresh_time)))::bigint,
   'stale',extract(epoch FROM now()-last_refresh_time)>
      coalesce((SELECT (value #>> '{}')::int FROM policies WHERE key='cash_stale_seconds'),10800)),
 'outflows_after_refresh',jsonb_build_object('total',dash_money(outflow,currency),
   'items',coalesce((SELECT jsonb_agg(jsonb_build_object('operation_id',id,'kind',kind,
      'amount',dash_money(amount_minor,totals.currency),'submitted_at',dash_time(submitted_at))
      ORDER BY submitted_at,id) FROM outflows),'[]')),
 'effective_available',dash_money(available_minor-outflow,currency),
 'cash_reservations',jsonb_build_object('total',dash_money(cash,currency),
   'by_kind',jsonb_build_object('RUN_CASH',dash_money(run_cash,currency),
      'PROPOSAL_CASH',dash_money(proposal_cash,currency))),
 'unreserved',dash_money(available_minor-outflow-cash,currency),
 'earmarks_not_subtracted',jsonb_build_object('dispute',dash_money(earmark,currency)),
 'unclaimed_outstanding',dash_money(unclaimed,currency),
 'formula','effective_available = available - outflows_after_refresh; unreserved = effective_available - cash_reservations'
) AS doc FROM totals
WHERE NOT EXISTS (SELECT 1 FROM outflows WHERE amount_minor IS NULL);
"""
    )
    _graph_and_privileges()


def _graph_and_privileges():
    # Security-definer graph access uses only this migration's fixed views and
    # a schema fixed at installation, never a caller-controlled search_path.
    connection = op.get_bind()
    schema = connection.exec_driver_sql("SELECT current_schema()").scalar_one()
    quoted_schema = connection.dialect.identifier_preparer.quote(schema)
    op.execute(f"""
ALTER FUNCTION dash_redact(jsonb) SET search_path = {quoted_schema}, pg_temp;
CREATE FUNCTION dash_money_graph(dispute_id uuid DEFAULT NULL,
 maker_id uuid DEFAULT NULL, run_id uuid DEFAULT NULL) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = {quoted_schema}, pg_temp AS $function$
BEGIN
 IF num_nonnulls($1,$2,$3)<>1 THEN
   RAISE EXCEPTION 'choose exactly one graph root' USING ERRCODE='22023';
 END IF;
 RETURN (
 WITH RECURSIVE nodes AS (
   SELECT 'dispute:'||d.id id,'dispute' type,d.paypal_dispute_id label,d.phase state,
     dash_money(d.amount_minor,d.currency) amount FROM disputes d
   UNION ALL SELECT 'order:'||o.id,'order',coalesce(o.tracking->>'order_ref',
     o.paypal_order_id,o.id::text),NULL,dash_money(o.amount_minor,o.currency) FROM orders o
   UNION ALL SELECT 'obligation:'||o.id,'obligation',m.display_name||' share',o.state,
     dash_money(o.amount_minor,o.currency) FROM obligations o JOIN makers m ON m.id=o.maker_id
   UNION ALL SELECT 'line:'||l.id,'line',m.display_name||' payout',l.state,
     dash_money(l.amount_minor,l.currency) FROM payout_lines l JOIN makers m ON m.id=l.maker_id
   UNION ALL SELECT 'attempt:'||a.id,'attempt','Attempt '||a.attempt_no,a.state,
     dash_money(a.amount_minor,a.currency) FROM payout_attempts a
   UNION ALL SELECT 'maker:'||m.id,'maker',m.display_name,NULL,NULL::jsonb FROM makers m
   UNION ALL SELECT 'reservation:'||r.id,'reservation',
     CASE r.kind WHEN 'DISPUTE_EARMARK' THEN 'Dispute earmark'
       WHEN 'RUN_CASH' THEN 'Run cash' ELSE 'Proposal cash' END,r.state,
     dash_money(r.amount_minor,r.currency) FROM reservations r
 ), edges AS (
   SELECT 'dispute:'||d.id source,'order:'||d.order_id target,'disputes' type
     FROM disputes d WHERE d.order_id IS NOT NULL
   UNION ALL SELECT 'order:'||o.order_id,'obligation:'||o.id,'creates'
     FROM obligations o WHERE o.order_id IS NOT NULL
   UNION ALL SELECT 'reservation:'||r.id,'obligation:'||r.obligation_id,'holds'
     FROM reservations r WHERE r.obligation_id IS NOT NULL AND r.state='ACTIVE'
   UNION ALL SELECT 'obligation:'||lo.obligation_id,'line:'||lo.line_id,'allocated_to'
     FROM payout_line_obligations lo WHERE lo.active
   UNION ALL SELECT 'line:'||a.line_id,'attempt:'||a.id,'attempted_by' FROM payout_attempts a
   UNION ALL SELECT 'obligation:'||o.id,'maker:'||o.maker_id,'owed_to' FROM obligations o
   UNION ALL SELECT 'line:'||l.id,'maker:'||l.maker_id,'pays' FROM payout_lines l
 ), roots AS (
   SELECT 'dispute:'||$1 id WHERE $1 IS NOT NULL
   UNION ALL SELECT 'maker:'||$2 WHERE $2 IS NOT NULL
   UNION ALL SELECT 'line:'||l.id FROM payout_lines l WHERE l.run_id=$3
   UNION ALL SELECT 'line:'||(replacement->>'line_id') FROM proposals p
    CROSS JOIN LATERAL jsonb_array_elements(coalesce(p.params->'replacements','[]')) replacement
    WHERE p.id=(SELECT latest.id FROM proposals latest
       WHERE latest.action='SCHEDULE_PAYOUT_RUN' AND latest.params->>'run_id'=$3::text
       ORDER BY latest.created_at DESC,latest.id DESC LIMIT 1)
 ), reached(id) AS (
   SELECT id FROM roots
   UNION SELECT CASE WHEN e.source=r.id THEN e.target ELSE e.source END
     FROM reached r JOIN edges e ON e.source=r.id OR e.target=r.id
 )
 SELECT dash_redact(jsonb_build_object(
   'nodes',coalesce((SELECT jsonb_agg(jsonb_build_object('id',n.id,'type',n.type,
      'label',n.label,'state',n.state,'amount',n.amount) ORDER BY n.id)
      FROM nodes n WHERE n.id IN (SELECT id FROM reached)),'[]'),
   'edges',coalesce((SELECT jsonb_agg(jsonb_build_object('from',e.source,'to',e.target,
      'type',e.type) ORDER BY e.source,e.target,e.type) FROM edges e
      WHERE e.source IN (SELECT id FROM reached) AND e.target IN (SELECT id FROM reached)),'[]')))
 );
END;
$function$;
REVOKE ALL ON FUNCTION dash_money_graph(uuid,uuid,uuid) FROM PUBLIC;
DO $role$ BEGIN
 IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='countersign_agent_ro') THEN
   CREATE ROLE countersign_agent_ro NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
     NOINHERIT NOREPLICATION NOBYPASSRLS;
 END IF;
END $role$;
GRANT USAGE ON SCHEMA {quoted_schema} TO countersign_agent_ro;
GRANT SELECT ON v_dash_audit,v_dash_proposals,v_dash_incidents,v_dash_incident_detail,
 v_dash_reservations,v_dash_makers,v_dash_disputes,v_dash_runs,v_dash_run_detail,
 v_dash_cash TO countersign_agent_ro;
GRANT EXECUTE ON FUNCTION dash_money_graph(uuid,uuid,uuid) TO countersign_agent_ro;
"""
    )


def downgrade():
    # The role is shared between independent app/test schemas. Leave the NOLOGIN
    # role in place; dropping schema objects automatically removes its grants.
    op.execute("""
DROP FUNCTION dash_money_graph(uuid,uuid,uuid);
DROP VIEW v_dash_cash,v_dash_run_detail,v_dash_runs,v_dash_disputes,v_dash_makers,
 v_dash_reservations,v_dash_incident_detail,v_dash_incidents,v_dash_proposals,v_dash_audit;
DROP FUNCTION dash_redact(jsonb),dash_money(bigint,text),dash_time(timestamptz);
"""
    )
