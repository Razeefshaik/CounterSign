-- Money: bigint minor units + char(3) currency. Mutable rows carry version (approval binding).
CREATE TABLE makers (
  id uuid PRIMARY KEY, display_name text NOT NULL,
  payout_email text, payout_email_verified_at timestamptz,          -- written only by owner endpoint
  version int NOT NULL DEFAULT 1, created_at timestamptz NOT NULL DEFAULT now());

CREATE TABLE orders (
  id uuid PRIMARY KEY, maker_id uuid NOT NULL REFERENCES makers, buyer_ref text NOT NULL,
  amount_minor bigint NOT NULL CHECK (amount_minor > 0), currency char(3) NOT NULL,
  paypal_order_id text UNIQUE, paypal_capture_id text UNIQUE, captured_at timestamptz,
  tracking jsonb,                        -- carrier, number, status, uploader, validated (as PayPal reports)
  version int NOT NULL DEFAULT 1);

CREATE TABLE obligations (
  id uuid PRIMARY KEY, maker_id uuid NOT NULL REFERENCES makers, order_id uuid REFERENCES orders,
  kind text NOT NULL CHECK (kind IN ('ORDER_SHARE','ADJUSTMENT')),
  amount_minor bigint NOT NULL, currency char(3) NOT NULL, due_date date NOT NULL,
  state text NOT NULL CHECK (state IN ('OPEN','RESERVED','SCHEDULED','IN_FLIGHT','SETTLED','VOID')),
  version int NOT NULL DEFAULT 1, updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (order_id, maker_id, kind));

CREATE TABLE payout_runs (
  id uuid PRIMARY KEY, week_start date NOT NULL UNIQUE,
  state text NOT NULL CHECK (state IN ('DRAFT','AWAITING_OWNER','APPROVED','EXECUTING','DONE','CANCELLED')),
  approval_id uuid, version int NOT NULL DEFAULT 1);

CREATE TABLE payout_lines (
  id uuid PRIMARY KEY, run_id uuid NOT NULL REFERENCES payout_runs, maker_id uuid NOT NULL REFERENCES makers,
  amount_minor bigint NOT NULL CHECK (amount_minor > 0), currency char(3) NOT NULL,
  state text NOT NULL CHECK (state IN ('APPROVED','PAYING','PAID','UNPAID','RELEASED')),
  version int NOT NULL DEFAULT 1);

CREATE TABLE payout_line_obligations (
  line_id uuid REFERENCES payout_lines, obligation_id uuid REFERENCES obligations,
  amount_minor bigint NOT NULL, active boolean NOT NULL DEFAULT true,
  PRIMARY KEY (line_id, obligation_id));
CREATE UNIQUE INDEX uq_obligation_one_active_line ON payout_line_obligations(obligation_id) WHERE active;

CREATE TABLE operations (                -- write-ahead record of every intended PayPal write
  id uuid PRIMARY KEY,
  kind text NOT NULL CHECK (kind IN ('PAYOUT_CREATE','PAYOUT_CANCEL','DISPUTE_EVIDENCE','DISPUTE_ACCEPT_CLAIM')),
  target_type text NOT NULL, target_id uuid NOT NULL,
  payload jsonb NOT NULL, payload_hash bytea NOT NULL,
  proposal_id uuid, approval_id uuid,
  state text NOT NULL CHECK (state IN ('PENDING','IN_FLIGHT','SUCCEEDED','FAILED','UNKNOWN','BLOCKED')),
  response jsonb, paypal_debug_id text, error text,
  created_at timestamptz NOT NULL DEFAULT now(), submitted_at timestamptz, completed_at timestamptz);

CREATE TABLE payout_attempts (
  id uuid PRIMARY KEY, line_id uuid NOT NULL REFERENCES payout_lines, attempt_no int NOT NULL,
  operation_id uuid NOT NULL REFERENCES operations,
  sender_batch_id text NOT NULL UNIQUE, request_id text NOT NULL UNIQUE, sender_item_id text NOT NULL,
  payee_email text NOT NULL, amount_minor bigint NOT NULL, currency char(3) NOT NULL,
  state text NOT NULL CHECK (state IN ('PREPARED','SUBMITTED','UNKNOWN','REJECTED',
        'PENDING','UNCLAIMED','ONHOLD','BLOCKED','SUCCESS','FAILED','RETURNED','REFUNDED','REVERSED')),
  paypal_batch_id text, paypal_item_id text UNIQUE, paypal_raw jsonb,
  prepared_at timestamptz NOT NULL DEFAULT now(), last_checked_at timestamptz,
  UNIQUE (line_id, attempt_no));
-- Payout state machine rule enforced by the DB: at most one live attempt per line
CREATE UNIQUE INDEX uq_one_live_attempt ON payout_attempts(line_id)
  WHERE state IN ('PREPARED','SUBMITTED','UNKNOWN','PENDING','UNCLAIMED','ONHOLD','BLOCKED');

CREATE TABLE disputes (
  id uuid PRIMARY KEY, paypal_dispute_id text NOT NULL UNIQUE, order_id uuid REFERENCES orders,
  status text NOT NULL, lifecycle_stage text, reason text,          -- mirrored verbatim, no CHECK
  amount_minor bigint, currency char(3), seller_response_due_date timestamptz,
  outcome_code text, amount_refunded_minor bigint,
  phase text NOT NULL CHECK (phase IN ('ACTIVE','WON','LOST_FULL','LOST_PARTIAL','CANCELLED','UNKNOWN_OUTCOME')),
  links jsonb, raw jsonb, last_synced_at timestamptz, version int NOT NULL DEFAULT 1);

CREATE TABLE reservations (
  id uuid PRIMARY KEY,
  kind text NOT NULL CHECK (kind IN ('DISPUTE_EARMARK','PROPOSAL_CASH','RUN_CASH')),
  affects_cash boolean GENERATED ALWAYS AS (kind <> 'DISPUTE_EARMARK') STORED,  -- never subtract holds twice
  obligation_id uuid REFERENCES obligations, dispute_id uuid REFERENCES disputes,
  proposal_id uuid, run_id uuid REFERENCES payout_runs,
  amount_minor bigint NOT NULL CHECK (amount_minor > 0), currency char(3) NOT NULL,
  state text NOT NULL CHECK (state IN ('ACTIVE','RELEASED','CONSUMED')),
  created_at timestamptz NOT NULL DEFAULT now(), closed_at timestamptz);

CREATE TABLE ledger_journals (id uuid PRIMARY KEY, operation_id uuid REFERENCES operations,
  occurred_at timestamptz NOT NULL, paypal_effective_at timestamptz, memo text NOT NULL);
CREATE TABLE ledger_postings (journal_id uuid NOT NULL REFERENCES ledger_journals, account text NOT NULL,
  amount_minor bigint NOT NULL, currency char(3) NOT NULL);
-- deferred constraint trigger: SUM(amount_minor) per (journal_id, currency) = 0
-- accounts: paypal:available, paypal:withheld, paypal:payouts_in_transit, paypal:unclaimed,
--           maker_payable:<id>, platform:fees, paypal:fees, buyer:refunds

CREATE TABLE incidents (id uuid PRIMARY KEY,
  kind text NOT NULL CHECK (kind IN ('DISPUTE','PAYOUT_ITEM','RUN_AT_RISK','RECONCILIATION')),
  subject jsonb NOT NULL, state text NOT NULL, opened_at timestamptz NOT NULL DEFAULT now(),
  closed_at timestamptz, version int NOT NULL DEFAULT 1);

CREATE TABLE proposals (id uuid PRIMARY KEY, incident_id uuid REFERENCES incidents,
  action text NOT NULL, params jsonb NOT NULL, evidence_refs jsonb NOT NULL, rationale text,
  expected_effect jsonb, model text NOT NULL, raw_output jsonb NOT NULL,
  verdict text CHECK (verdict IN ('ALLOW','BLOCK','NEEDS_OWNER')), verdict_rules text[], verdict_details jsonb,
  state text NOT NULL, created_at timestamptz NOT NULL DEFAULT now());

CREATE TABLE approvals (id uuid PRIMARY KEY, proposal_id uuid NOT NULL REFERENCES proposals,
  payload_hash bytea NOT NULL, record_versions jsonb NOT NULL,   -- {"obligation:<id>": 3, "maker:<id>": 2, ...}
  approved_by text NOT NULL, approved_at timestamptz NOT NULL, expires_at timestamptz NOT NULL,
  used_at timestamptz, used_by_operation_id uuid UNIQUE);          -- single use

CREATE TABLE webhook_inbox (event_id text PRIMARY KEY, event_type text NOT NULL,
  resource_type text, resource_id text, headers jsonb NOT NULL, raw_body bytea NOT NULL,  -- exact bytes for signature
  payload jsonb NOT NULL, paypal_create_time timestamptz, received_at timestamptz NOT NULL DEFAULT now(),
  signature text NOT NULL CHECK (signature IN ('UNVERIFIED','SUCCESS','FAILURE')),
  status text NOT NULL CHECK (status IN ('RECEIVED','PROCESSED','IGNORED','REJECTED','ERROR')),
  processed_at timestamptz, attempts int NOT NULL DEFAULT 0, error text);

CREATE TABLE balance_snapshots (id uuid PRIMARY KEY, fetched_at timestamptz NOT NULL,
  as_of_time timestamptz, last_refresh_time timestamptz, currency char(3) NOT NULL,
  available_minor bigint, withheld_minor bigint, total_minor bigint, raw jsonb NOT NULL);

CREATE TABLE policies (key text PRIMARY KEY, value jsonb NOT NULL, version int NOT NULL DEFAULT 1,
  updated_at timestamptz NOT NULL DEFAULT now());   -- refund limits, reserve rule, autonomy tiers

CREATE TABLE audit_log (id bigserial PRIMARY KEY, at timestamptz NOT NULL DEFAULT now(),
  actor text NOT NULL CHECK (actor IN ('agent','owner','shield','executor','system')),
  kind text NOT NULL, proposal_id uuid, operation_id uuid, rule text, verdict text, details jsonb);  -- guardrail monitor

