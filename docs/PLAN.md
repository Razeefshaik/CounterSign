# CounterSign: day one sandbox plan, design, and handoff review

## Context

The repo holds only `docs/HANDOFF.md` (v2, the source of truth), `docs/DECISIONS.md`, `CLAUDE.md`, `.gitignore` (which covers `.env`) and `.env`. There is no code yet. The owner asked for three things before any application code is written:

1. A day one sandbox verification plan covering HANDOFF section 9.
2. A repository structure, a PostgreSQL schema for section 5, and code-level state machines.
3. A review of the handoff that flags anything wrong or unverifiable, with links to PayPal docs.

On Oct 23 the owner renamed the project from "OnceGuard Money Ops" to **CounterSign** (Python package `countersign`).

Every PayPal behaviour that section 9 marks "verify" is treated as unknown below. Any design element that depends on one is tagged **[pending Dn]**, where Dn is the day one check that will settle it. PayPal facts were checked against developer.paypal.com and the agent-toolkit GitHub repo on Oct 23, 2026. Links are given in section 3.

---

## 0. First actions after approval (docs only)

1. **Rename.** In `CLAUDE.md` and `HANDOFF.md`, change "OnceGuard Money Ops" to "CounterSign", including the claims language in section 12 ("settled at most once inside CounterSign"). Leave "Airwallex OnceGuard" in section 1 unchanged, because that is a separate project.
2. **DECISIONS.md.** Add one-line entries for:
   - the rename (the reason comes from the owner; see section 4)
   - each design change listed in section 3 (F1, F2, F6, F12, F15, F16, F18)
   - payout lines (section 2.2)
   - the LangChain adapter with a swappable model, and the mock-only baseline (section 4)
   - building the vertical slice first, with the transition trigger, `allowed_transitions` and property tests deferred until after it (section 4a)
3. **New `docs/SANDBOX_FINDINGS.md`.** One row per check with these columns: ID | handoff claim | observed | evidence file | verdict (CONFIRMED / DIFFERENT / BLOCKED) | design impact.
4. **New `.env.example`.** Variable names only (section 1.1, S5). I will not read or print `.env`.

---

## 1. Day one sandbox verification (Oct 23 to 25)

### 1.1 Setup

**S1. Sandbox accounts** (developer dashboard, Sandbox, Accounts; all US and USD)

| Alias | Type | Purpose |
|---|---|---|
| `mp-verify` | Business, about $5,000 balance | All day one experiments |
| `mp-demo` | Business | Created now and **left untouched**, so the recorded demo week starts with a clean history and balance |
| `buyer-1` (optionally `buyer-2`) | Personal, with PayPal balance and a card | Pays orders and opens disputes |
| `maker-a`, `maker-b`, `maker-c` | Personal | Payees |
| `unreg-1`, `unreg-2`, `unreg-3` | Addresses only, e.g. `cs-unreg-0N@example.com`, **never registered** as sandbox accounts | UNCLAIMED tests |

**S2. REST app on `mp-verify`.** Enable the Payouts and Transaction Search features and record which features are on. Optionally create a second app, `reader`, with Payouts disabled, to test whether its token loses the payouts scope. If it does, the agent's read tools can run on credentials that cannot move money (extra protection on top of the allowlist). **[pending D1]**

**S3. Webhooks.**
- Run a public HTTPS tunnel (cloudflared or ngrok) to the local catcher at `/webhooks/paypal`.
- Register a webhook for `PAYMENT.PAYOUTS-ITEM.*`, `PAYMENT.PAYOUTSBATCH.*`, `CUSTOMER.DISPUTE.*` and `PAYMENT.CAPTURE.*`.
- Put the `webhook_id` in `.env`.

**S4. No database on day one.** The harness writes JSON files only. Dependencies: `httpx`, `fastapi` and `uvicorn` (for the catcher), and `python-dotenv`. Python 3.12, because the toolkit needs 3.11 or later.

**S5. `.env` variable names:** `PAYPAL_ENV=sandbox`, `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET`, `PAYPAL_WEBHOOK_ID`, `SBX_BUYER_EMAIL`, `SBX_MAKER_{A,B,C}_EMAIL`, `SBX_UNREG_EMAILS`, `DATABASE_URL` (used later).

### 1.2 Harness (`scripts/sandbox_checks/`)

- **Runner.** `python -m scripts.sandbox_checks run D4` runs the steps of one check in order.
- **Manual steps.** Steps such as buyer approval or opening a case in the Resolution Center print instructions, then wait for Enter and the IDs.
- **Recorder.** Every HTTP exchange is saved to `recordings/sandbox/<date>/<check>/<NN>_<step>.json` with:
  - method, path, and request headers **without** `Authorization`
  - request body, response status, response headers (including `paypal-debug-id`) and response body
  - `t_sent` and `t_recv`
- **Webhook catcher.** Stores the raw body bytes, headers and `received_at` for each event.
- **Balance poller.** Runs in the background every 5 minutes while any check is running and appends to `balances.jsonl`.
- **Redaction before commit.** Tokens, client ID and secret are removed, and real emails are replaced by role aliases. The repo is public, and the recordings become fixtures for the mock.

### 1.3 Checks (record everything; nothing is assumed)

**D1. Auth and scopes** (gates every other check)
- Call `POST /v1/oauth2/token`.
- Record the `scope` string: payouts, disputes read/update-seller, disputes/create, and reporting.
- Pass: Payouts, disputes and reporting scopes are present.

**D2. Money in** (needed because disputes require real transactions)
- Create three Orders v2 orders (`POST /v2/checkout/orders`): $400 for maker A, $60 for maker B and $25 spare. `buyer-1` approves each in the browser, then capture with `POST /v2/checkout/orders/{id}/capture`.
- Record:
  - the capture ID, status and `seller_receivable_breakdown` (sandbox fees)
  - the delay of the `PAYMENT.CAPTURE.COMPLETED` webhook
  - when the balance changes
- Add tracking to the $400 order through Orders v2 `POST /v2/checkout/orders/{id}/track`. The toolkit's create tool uses the deprecated trackers-batch endpoint.
- Record what `GET /v1/shipping/trackers?transaction_id=` returns, especially `status`, `shipment_uploader` and `tracking_number_validated`.

**D3. Payout happy path**
- Create a single-item batch to maker A with derived keys.
- Record:
  - the 201 body: `batch_status`, and **whether item IDs are present** (docs say not)
  - `GET /v1/payments/payouts/{batch_id}` polled with timestamps until the item settles, plus `GET .../payouts-item/{id}`
  - webhook names, order and delay
  - the balance delta

**D4. UNCLAIMED is reliable** (section 9 verify #1)
- Send three single-item payouts, one to each of `unreg-1..3`.
- Record the status timeline of each. Pass: all three reach UNCLAIMED.
- Also record whether **available** drops and whether **withheld** changes while an item is UNCLAIMED. DECISIONS.md marks this "confirm in sandbox", and the docs do not state it.

**D5. Cancel** (verify #2)
- Cancel the `unreg-1` item with `POST /v1/payments/payouts-item/{id}/cancel`.
- Record:
  - the `transaction_status` in the cancel response
  - the GET status afterwards (RETURNED or something else)
  - which webhooks fire (CANCELED and/or RETURNED) and in what order
  - when available reflects the returned money
- Negative cases: cancel the same item again, and cancel the SUCCESS item from D3. Record the error names.
- Leave the `unreg-2` item uncancelled and keep recording its status until the deadline.
- Optional race fixture: create a sandbox personal account at `unreg-3`'s address and see whether the item becomes SUCCESS.

**D6. Idempotency and resolving UNKNOWN** (the design hinges on this; see F1)
- **(a) Exact replay.** Same `PayPal-Request-Id`, same `sender_batch_id`, same body. Record the HTTP code and whether the `payout_batch_id` is the same.
- **(b) New `PayPal-Request-Id`, same `sender_batch_id`.** Record the error name and code, and follow the HATEOAS link to confirm it points at the original batch.
- **(c) Same `PayPal-Request-Id`, different body.** Record which one wins.
- **(d) Two concurrent POSTs with the same keys** (`asyncio.gather`). Record both responses, then confirm by GET that only one item exists.
- **(e) Lost response.** POST with a read timeout of about 50 ms, so the request is sent but the response is dropped. Mark the attempt UNKNOWN, resolve it with (a), and confirm one payment by GET and balance. These payloads are the fixture for live safety moment 1.
- Each sub-check uses $1 to maker B and fresh keys.

**D7. Payout negative-test values** (fixtures for the fault harness; the value goes in `items[0].note` or the URL path)
- Create: `ERRPYO005` INSUFFICIENT_FUNDS, `006` INTERNAL_ERROR, `014` USER_BUSINESS_ERROR, `036` REQUEST_TIMEOUT_EXCEEDED, `039` PREVIOUS_REQUEST_IN_PROGRESS.
- GET batch: `ERRPYOB002/003/004` (PENDING, PROCESSING, DENIED).
- GET item: `ERRPYO015` (FAILED/CLOSED_MARKET), `047` RECEIVER_UNREGISTERED, `052` RISK_DECLINE.
- Cancel: `ERRPYO007/008/009`.
- There are **no test values** for item ONHOLD, BLOCKED, PENDING, UNCLAIMED, RETURNED or REFUNDED (see F13).

**D8. Dispute creation** (verify #3)
- **(a) API route.** Possible only if D1 shows the `disputes/create` scope. That scope needs a PayPal account manager to add it (F5). If it is absent, record BLOCKED and do not chase it.
- **(b) UI route.** Log in as `buyer-1` at sandbox.paypal.com and use the Resolution Center to open INR (item not received) on the $400 capture and SNAD (not as described) on the $60 capture. Record:
  - the submit time, and the time until the dispute first appears in `GET /v1/customer/disputes` (polled every minute)
  - from the full GET: `status`, `dispute_life_cycle_stage`, `reason`, `dispute_amount`, `seller_response_due_date`
  - which `disputed_transactions` field equals our capture ID
  - all HATEOAS `rel`s
  - the delay of the `CUSTOMER.DISPUTE.CREATED` webhook
- **(c) Hold** (verify #5: *whether* a hold happens, not only how fast). Record the capture status from `GET /v2/payments/captures/{id}` and available/withheld every 5 minutes for 3 hours.

**D9. Evidence** (the demo depends on it; F7)
- If the INR dispute has a `provide-evidence` link, call `POST .../provide-evidence` with `PROOF_OF_FULFILLMENT` and the tracking info.
- Try multipart (with an `input` JSON part) first, then JSON. Record which format works, plus the resulting status, stage and webhook.
- If the link is absent at INQUIRY, record which links are present and do D10's path first. Record **which stage allows evidence**; the owner will adjust the demo.

**D10. Settle** (verify #4; F8)
- Find a path to UNDER_REVIEW, recording status and stage after each step: after evidence (poll), after `POST .../escalate`, and after a buyer-side escalation in the UI.
- **Fallback (owner):** the buyer closes or cancels a dispute in the Resolution Center. Record whether it resolves, the `outcome_code`, and whether the funds are released and when.
- When the dispute is UNDER_REVIEW and has an `adjudicate` link, adjudicate the $400 dispute with `SELLER_FAVOR`. Record:
  - the `dispute_outcome`
  - the `CUSTOMER.DISPUTE.RESOLVED` webhook
  - when the hold is released in the balance
- $60 dispute: `accept-claim` from the script (standing in for the owner). Record the refund, `outcome_code`, `amount_refunded` and the balance change.
- $25 dispute: `require-evidence`, then adjudicate with `BUYER_FAVOR`.
- Catalogue every `outcome_code` seen.

**D11. Webhooks** (verify #6; runs during all checks)
- Per event, record:
  - delay (`received_at` minus `create_time`)
  - duplicates and order
  - events that never arrived (compared with transitions seen by polling)
- Call `POST /v1/notifications/verify-webhook-signature` on every real event and record `verification_status`.
- Send one simulator event. It is expected to fail postback verification (F11).
- Try `GET /v1/notifications/webhooks-events` and one `.../resend`. If they work, they serve as a backfill source for the poller and as a generator for duplicate-delivery faults.

**D12. Balance freshness** (verify #5)
- From `balances.jsonl`, compare `last_refresh_time` and `as_of_time` with wall time.
- For each event type (capture, payout SUCCESS, UNCLAIMED, return, dispute hold, release, refund), find the first snapshot that reflects it.
- Check whether available + withheld = total.
- Measure the lag of `GET /v1/reporting/transactions` once.

**D13. Agent Toolkit is read-only** (hard rule)
- In a throwaway venv, install `paypal-agent-toolkit==1.11.0`.
- Use `Configuration(actions={"disputes":{"list":True,"get":True}, "shipment":{"get":True}, "orders":{"get":True}, "transactions":{"list":True}})`.
- List the tools the chosen adapter exposes, and assert that the set exactly equals `{list_disputes, get_dispute, get_shipment_tracking, get_order_details, list_transactions}`.
- Call each tool once against the sandbox and record the output.
- **Never set `disputes.create`**: that key enables `accept_dispute_claim` (F9).

**Order:**
- Oct 23: S1 to S3, D1, D2, D3, start D4, D7, D13.
- Oct 24: D5, D6, D8, with D11 and D12 running in the background.
- Oct 25: D9 and D10, then the findings doc, an update to HANDOFF section 9, and the DECISIONS entries.

**Exit criteria:** every section 9 "verify" item has a verdict and an evidence file. Every DIFFERENT verdict has a DECISIONS entry and a design change **before** core step 2. I report the findings to you at that checkpoint.

---

## 2. Design

### 2.1 Repository structure

```
CounterSign/
├─ pyproject.toml            # Python 3.12; ruff, mypy, pytest, hypothesis, import-linter
├─ .env.example  LICENSE (Apache-2.0, same as Agent Toolkit)  README.md  docker-compose.yml (postgres:16)
├─ docs/  HANDOFF.md  DECISIONS.md  SANDBOX_FINDINGS.md
├─ migrations/               # Alembic, raw SQL
├─ src/countersign/
│  ├─ config.py  money.py    # Money(minor:int, currency); no floats anywhere
│  ├─ db/                    # pool, tx(), cas_transition(), repositories
│  ├─ fsm/                   # obligation, line, attempt, operation, dispute, proposal, incident
│  ├─ paypal/
│  │  ├─ http.py             # OAuth, debug-id capture, recorder hook
│  │  ├─ read.py             # GET only: payouts, disputes, balances, tracking, webhook events
│  │  ├─ write.py            # payout create/cancel, provide evidence, accept claim: EXECUTOR ONLY
│  │  ├─ webhook_verify.py
│  │  └─ sandbox_controls.py # require-evidence/adjudicate: scripts and demo bar only
│  ├─ inbox/  reconcile/  ledger/  approvals/  simulator/  incidents/
│  ├─ shield/                # snapshot.py, engine.py, rules/<one file per rule>.py
│  ├─ executor/              # protocol.py
│  ├─ agent/                 # tools.py (allowlist), commander.py, proposal.py, prompts/
│  ├─ api/                   # FastAPI: webhooks, owner approvals, dashboard JSON
│  ├─ worker.py  main.py     # one process: API + async loop (inbox, poller, executor queue)
├─ web/                      # dashboard: AG Grid Community, static, served by FastAPI
├─ mock_paypal/              # local mock built from recordings + fault injection
├─ eval/                     # scenarios/, systems/{baseline_llm,rules_only,countersign}, metrics, report
├─ scripts/sandbox_checks/   # day one harness
├─ recordings/sandbox/       # redacted payloads (mock fixtures)
└─ tests/ unit/ (fsm, shield, money, approvals)  integration/ (executor + postgres + mock)  contract/ (parse recordings)
```

**How the hard rules are enforced in code** (CI fails if any check breaks):

- **Import boundaries** (import-linter contracts):
  - `countersign.agent` may not import `paypal.write`, `paypal.sandbox_controls`, `executor` or `db`.
  - Only `executor` may import `paypal.write`.
- **Toolkit allowlist at startup.** `agent/tools.py` asserts the exact tool name set from D13 and refuses to start if it differs. A unit test covers this. The tool list is built through the toolkit's LangChain adapter, and the model sits behind a swappable interface.
- **Deferred until the vertical slice works (owner, Oct 23):** the `allowed_transitions` table and its trigger, and the hypothesis property tests. The import-linter contracts and the allowlist test start on day one.
- **Read-only database role.** The agent's `query_money_graph` runs fixed parameterised queries over views, using a separate role (`countersign_agent_ro`) that has SELECT only.

### 2.2 PostgreSQL schema (section 5)

The schema has one change from the handoff, **payout lines**. Obligations are per order share, but one payout item pays a maker a *sum* of shares (maker B's $486 covers several orders). So:

- a **payout line** (maker × run) groups obligations;
- an **attempt** is one PayPal item for a line;
- keys derive from `line_id` + `attempt_no`, not from the obligation ID.

Two further choices:

- **Replacement is a new attempt on the same line.** Last week's line for maker C gets attempt 2, sent to the owner-corrected email.
- **IDs are UUIDv7**, so resetting the dev database cannot reuse a `sender_batch_id` from the previous 30 days (F15).

```sql
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
CREATE TABLE ledger_postings (journal_id uuid REFERENCES ledger_journals, account text NOT NULL,
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

-- AFTER THE VERTICAL SLICE (owner, Oct 23): allowed_transitions table + trigger
CREATE TABLE allowed_transitions (entity text, from_state text, to_state text,
  PRIMARY KEY (entity, from_state, to_state));
-- BEFORE UPDATE OF state trigger on obligations/payout_lines/payout_attempts(our states only)/operations
-- rejects pairs not in allowed_transitions; rows are generated from fsm/*.py (a test asserts no drift)
```

**Money graph view.** The dashboard view `v_money_graph` joins dispute → order → obligation → line → attempt → maker.

### 2.3 State machines (code-level)

The same pattern applies to every entity:

- a `StrEnum` for states and another for events;
- a transition table `dict[(State, Event), State]`;
- `cas_transition(conn, table, id, expected_state, expected_version, event)`, which runs `UPDATE … SET state=$new, version=version+1 WHERE id=$id AND state=$exp AND version=$v` and raises if zero rows change;
- **after the vertical slice:** a database trigger that checks the transition against `allowed_transitions` as a second guard. Until then, the CAS update and the Python tables are the guard.

```python
# fsm/obligation.py
class Obl(StrEnum): OPEN, RESERVED, SCHEDULED, IN_FLIGHT, SETTLED, VOID
OBLIGATION = {
  (OPEN,      DISPUTE_OPENED):   RESERVED,
  (OPEN,      LINE_APPROVED):    SCHEDULED,
  (OPEN,      ORDER_REFUNDED):   VOID,
  (RESERVED,  DISPUTE_WON):      OPEN,
  (RESERVED,  DISPUTE_CANCELLED):OPEN,
  (RESERVED,  DISPUTE_LOST_PARTIAL): OPEN,     # same tx: ADJUSTMENT obligation for the refunded share (F16)
  (RESERVED,  DISPUTE_LOST_FULL):VOID,
  (SCHEDULED, DISPUTE_OPENED):   RESERVED,     # line amount changes -> run approval voided by version check
  (SCHEDULED, LINE_RELEASED):    OPEN,         # owner defers or run is cancelled before submit
  (SCHEDULED, ATTEMPT_PREPARED): IN_FLIGHT,
  (IN_FLIGHT, ATTEMPT_PAID):     SETTLED,      # attempt GET-confirmed SUCCESS
  (IN_FLIGHT, ATTEMPT_NOT_PAID): SCHEDULED,    # GET-confirmed FAILED / RETURNED / REJECTED; line -> UNPAID
  (SETTLED,   PAYOUT_CLAWED_BACK): OPEN,       # GET-confirmed REFUNDED / REVERSED on the settling item
}
# IN_FLIGHT + DISPUTE_OPENED: no transition; opens incident PAID_BEFORE_DISPUTE (owner task).
# UNCLAIMED keeps IN_FLIGHT, so SETTLED only ever means "PayPal says SUCCESS".

# fsm/line.py
LINE = {(APPROVED, ATTEMPT_PREPARED): PAYING, (PAYING, ATTEMPT_PAID): PAID,
        (PAYING, ATTEMPT_NOT_PAID): UNPAID, (UNPAID, REPLACEMENT_APPROVED): PAYING,
        (APPROVED, RELEASED): RELEASED, (UNPAID, RELEASED): RELEASED}

# fsm/attempt.py
class Att(StrEnum):
    PREPARED, SUBMITTED, UNKNOWN, REJECTED                       # ours
    PENDING, UNCLAIMED, ONHOLD, BLOCKED                          # PayPal, non-terminal
    SUCCESS, FAILED, RETURNED, REFUNDED, REVERSED                # PayPal item transaction_status
LIVE = {PREPARED, SUBMITTED, UNKNOWN, PENDING, UNCLAIMED, ONHOLD, BLOCKED}
OURS = {
  (PREPARED, HTTP_2XX):            SUBMITTED,  # batch id known; item id comes from GET batch (F1)
  (PREPARED, HTTP_DEFINITE_4XX):   REJECTED,   # class list fixed from D6/D7 recordings [pending D6, D7]
  (PREPARED, AMBIGUOUS):           UNKNOWN,    # timeout, conn reset, 5xx
  (UNKNOWN,  REPLAY_CONFIRMED):    SUBMITTED,  # identical keys+body replay, or duplicate error whose
                                               # HATEOAS link GETs to a batch matching payee+amount [pending D6]
}
# From SUBMITTED onward the state MIRRORS PayPal's GET item transaction_status. A webhook never moves
# state; it triggers a GET. Expected mirror moves (others are recorded AND open an incident; never dropped):
PAYPAL_EXPECTED = {PENDING: {SUCCESS, FAILED, UNCLAIMED, ONHOLD, BLOCKED},
                   ONHOLD: {PENDING, SUCCESS, FAILED, BLOCKED, RETURNED},
                   UNCLAIMED: {SUCCESS, RETURNED}, SUCCESS: {REFUNDED, REVERSED}}   # [pending D3-D5]
WEBHOOK_TO_STATUS = {"SUCCEEDED": SUCCESS, "HELD": ONHOLD, "UNCLAIMED": UNCLAIMED, "FAILED": FAILED,
                     "BLOCKED": BLOCKED, "RETURNED": RETURNED, "REFUNDED": REFUNDED,
                     "CANCELED": None, "DENIED": None}  # None = GET decides [pending D5, D7]
UNKNOWN_REPLAY_WINDOW = timedelta(days=25)   # under the documented 30-day key window; after it -> owner

# fsm/dispute.py: status mirrored verbatim; CounterSign acts on the derived phase
def phase(d) -> Phase: ...  # RESOLVED + outcome_code via OUTCOME_MAP; unmapped -> UNKNOWN_OUTCOME
OUTCOME_MAP: dict[str, Phase] = {}  # filled ONLY from codes recorded in D10 [pending D10]
# UNKNOWN_OUTCOME keeps the reserve (fail closed, scoped to this dispute) and opens an incident.

# fsm/operation.py
OPERATION = {(PENDING, LOCK_AND_RECHECK_OK): IN_FLIGHT, (PENDING, RECHECK_FAILED): BLOCKED,
             (IN_FLIGHT, OK): SUCCEEDED, (IN_FLIGHT, DEFINITE_ERROR): FAILED, (IN_FLIGHT, AMBIGUOUS): UNKNOWN,
             (UNKNOWN, RESOLVED_OK): SUCCEEDED, (UNKNOWN, RESOLVED_NOT_DONE): FAILED}

# fsm/proposal.py
# CREATED -> ALLOWED | BLOCKED | NEEDS_OWNER;  NEEDS_OWNER -> APPROVED | REJECTED | EXPIRED;
# ALLOWED|APPROVED -> EXECUTING -> EXECUTED | BLOCKED_AT_EXECUTION | FAILED

# fsm/incident.py
# OPEN -> INVESTIGATING -> AWAITING_OWNER | EXECUTING | WAITING_EXTERNAL -> ... -> RESOLVED
# RESOLVED only when the reconciler agrees: obligations, reservations and GET-confirmed PayPal states match.
```

**Shield** (`shield/engine.py`)

```python
class Verdict(StrEnum): ALLOW, BLOCK, NEEDS_OWNER
Rule = Callable[[Proposal, Snapshot], RuleResult | None]  # pure function; no I/O
def evaluate(p, snap, *, at_execution: bool) -> Decision:
    results = [r for rule in RULES if (r := rule(p, snap))]
    # any BLOCK -> BLOCK (all failing rule names); else any NEEDS_OWNER -> NEEDS_OWNER
    # (ALLOW at execution only if a valid bound approval exists); else ALLOW
```

**Funding formula** (`ledger/cash.py`; Funding rule; F12). It stays conservative because the balance lags:

```
effective_available = snapshot.available
                      − Σ outflows we submitted after snapshot.last_refresh_time   (payout attempts, refunds)
                      (+ inflows only once a snapshot reflects them)
unreserved = effective_available − Σ ACTIVE reservations WHERE affects_cash
```

DISPUTE_EARMARK never touches cash, because PayPal already withholds that money **[pending D8c, D12]**.

**Executor protocol** (`executor/protocol.py`)

1. **Transaction 1** (READ COMMITTED with `SELECT … FOR UPDATE` on the operation, proposal, approval, obligations, line, attempts, maker and dispute):
   - build the snapshot and re-run every rule;
   - check the approval binding: hash, versions, expiry and unused;
   - mark the approval used, insert the attempt as PREPARED with derived keys, set the operation to IN_FLIGHT, and COMMIT.
2. **HTTP call** via `paypal.write`, outside any transaction. Concurrency safety comes from `uq_one_live_attempt` and the operation's state.
3. **Transaction 2:** record the response, move the attempt to SUBMITTED, REJECTED or UNKNOWN, and write the ledger journal (`payouts_in_transit`). UNKNOWN is queued for the reconciler, which only replays with identical keys.

---

## 3. Handoff review: wrong, overstated or unverifiable

**Outcome note (Oct 27):** This is the approved day-one plan and historical review. Sandbox findings and check outcomes are in [SANDBOX_FINDINGS.md](SANDBOX_FINDINGS.md); adopted decisions are recorded in [DECISIONS.md](DECISIONS.md).

**High impact** (changes the design or the demo):

- **F1. "UNKNOWN resolved by querying PayPal by item id or by sender_batch_id" (sections 5 and 7).** Payouts has **no lookup by `sender_batch_id`**. The only lookups are GET batch by `payout_batch_id` and GET item by `payout_item_id`, and the create response returns **no item IDs**. After a lost response you hold neither ID.
  - Documented resolution paths:
    - retry with the same `PayPal-Request-Id`: "PayPal returns the current state of the payout";
    - reuse the same `sender_batch_id`: the request is rejected "with a HATEOAS link to the original payout".
  - Both are idempotent POST replays, and both only work within the 30-day key window.
  - Proposal: allow resolving UNKNOWN **only by replaying identical keys and body** within 25 days, and verify any linked batch's payee and amount before adopting it. Beyond the window, hand it to the owner and use Transaction Search. Reword section 5 accordingly.
  - Links: [create batch](https://developer.paypal.com/api/payments.payouts-batch/v1/payouts-post) · [API endpoints](https://developer.paypal.com/docs/api/payments.payouts-batch/v1/) · [customize/idempotency](https://developer.paypal.com/docs/payouts/standard/integrate-api/customize/)
- **F2. "Cancel before replace: item RETURNED or CANCELED" (section 7).** CANCELED is **not** an item `transaction_status`. The documented statuses are SUCCESS, FAILED, PENDING, UNCLAIMED, RETURNED, ONHOLD, BLOCKED, REFUNDED and REVERSED. RETURNED "occurs when the recipient does not claim the payout or you cancel it". CANCELED exists only as a webhook name; DENIED is a batch status and a webhook name. The rule should read "GET shows RETURNED" **[pending D5]**.
  - Links: [item statuses](https://developer.paypal.com/payouts/use-payouts/track-payout-item-status) · [cancel](https://developer.paypal.com/api/payments.payouts-batch/v1/payouts-item-cancel) · [webhook names](https://developer.paypal.com/api/rest/webhooks/event-names)
- **F5. "Create dispute (buyer credentials via Log in with PayPal)" (section 9).** The handoff omits the requirement that "Contact your PayPal account manager to add" the `DISPUTE_CREATE` scope, plus a `PayPal-Auth-Assertion` JWT. A hackathon account probably cannot get this. The documented alternative is for the buyer to open the case in the sandbox Resolution Center UI. The plan uses the UI route and labels it on screen as staged.
  - Links: [disputes set-up](https://developer.paypal.com/platforms/disputes/set-up) · [test and go live](https://developer.paypal.com/disputes/test-go-live)
- **F7. "PROVIDE_EVIDENCE autonomously" on an item-not-received case (sections 6 and 10).** The docs conflict:
  - the lifecycle reference lists provide evidence as a **claim-stage** seller action;
  - the sandbox guide tests it on INR cases at inquiry;
  - the endpoint says multipart while the prose says JSON.
  - Demo moment 0:35 to 1:05 depends on it **[pending D9]**.
  - Links: [lifecycle reference](https://developer.paypal.com/docs/disputes/disputes-reference/) · [provide evidence](https://developer.paypal.com/api/customer-disputes/v1/disputes-provide-evidence)
- **F8. "Settle (from UNDER_REVIEW)" (sections 9 and 10).** This part is correct, but **no doc says how a dispute gets into UNDER_REVIEW**. Escalate moves the stage from INQUIRY to CHARGEBACK, but its resulting status is not stated. The "following week" demo step depends on finding that path **[pending D10]**.
  - Links: [escalate](https://developer.paypal.com/api/customer-disputes/v1/disputes-escalate) · [test and go live](https://developer.paypal.com/disputes/test-go-live)
- **F9. Agent Toolkit details (sections 6 and 9).**
  - In Python, `accept_dispute_claim` is switched on by the config key **`disputes.create`**. Anyone enabling "create" for disputes silently grants a refund tool.
  - `get_order` is actually `get_order_details`.
  - Python adapters exist for OpenAI Agents SDK, LangChain, CrewAI and Bedrock only, with **no Anthropic module**.
  - Python 3.11 or later is required, and the latest version is 1.11.0.
  - Mitigation: an exact-name allowlist assertion at startup (section 2.1).
  - Links: [tools.py](https://github.com/paypal/agent-toolkit/blob/main/python/paypal_agent_toolkit/shared/tools.py) · [configuration.py](https://github.com/paypal/agent-toolkit/blob/main/python/paypal_agent_toolkit/shared/configuration.py) · [PyPI](https://pypi.org/project/paypal-agent-toolkit/)
- **F6. "A buyer dispute freezes the funds" (section 3, pain 1; section 9, "Holds").** This is overstated. PayPal documents a **"NO HOLD"** dispute scenario, where losing means the merchant's account is debited. Card chargebacks are issuer **reversals**, not holds. The dispute reserve stays (it is an earmark either way), but cash handling must read the actual withheld amount and capture status. Reword pain 1 as "may freeze".
  - Links: [integration guide](https://developer.paypal.com/docs/disputes/integration-guide/) · [test and go live](https://developer.paypal.com/disputes/test-go-live)
- **F4. "Available already excludes money in unclaimed payouts" (section 8, DECISIONS "consistent with PayPal docs").** PayPal's docs **do not state this**. They only say the money returns on cancel or after 30 days. The demo table's Monday $1,500 depends on it **[pending D4]**.
  - Link: [unclaimed payouts](https://developer.paypal.com/payouts/use-payouts/handle-unclaimed-payouts)
- **F18. Evaluation baseline vs the hard rules (section 11).** Baseline system 1 is "a plain LLM agent calling PayPal directly". That conflicts with "the AI agent never calls money moving PayPal APIs" and with "never enable Toolkit write tools". Proposal: the baseline runs **only against `mock_paypal`**, with mock-only tools. It never receives sandbox credentials or Toolkit write tools, and the report says so.

**Medium impact:**

- **F10. "Tracking shows delivery" as strong evidence (sections 4 and 10).** `get_shipment_tracking` calls `GET /v1/shipping/trackers?transaction_id=`. That returns tracking the **merchant uploaded**, including a status the uploader chose. `tracking_number_validated` only says the carrier validated the number. The AI's evidence-strength judgement and the README must say this. The toolkit's create tool also uses the deprecated `trackers-batch` endpoint.
  - Links: [tracking API](https://developer.paypal.com/docs/api/tracking/v1/) · [toolkit handler](https://github.com/paypal/agent-toolkit/blob/main/python/paypal_agent_toolkit/shared/tracking/tool_handlers.py)
- **F13. "Mock built from recorded sandbox payloads" (section 11).** Sandbox negative-test values exist for FAILED items, PENDING/PROCESSING/DENIED batches, INSUFFICIENT_FUNDS and timeouts. There are **none for item ONHOLD, BLOCKED, PENDING, UNCLAIMED, RETURNED or REFUNDED**, so those fixtures will be synthesised from the documented schema and labelled "synthesised" in the eval report.
  - Links: [Payouts test values](https://developer.paypal.com/api/payouts/v5/standard/test-go-live/) · [negative testing](https://developer.paypal.com/tools/sandbox/negative-testing/test-values/)
- **F11. "Webhook authenticity: verify API" (section 7).** This is correct for real events. However, **simulator events cannot be verified by postback** (their mock webhook ID is the literal `WEBHOOK_ID`), and verification needs the exact raw body bytes. So the mock and fault harness need a test-only signer with self-verification, and sandbox webhooks need a public HTTPS tunnel.
  - Links: [webhook simulator](https://developer.paypal.com/api/rest/webhooks/simulator) · [integrate webhooks](https://developer.paypal.com/api/rest/webhooks/rest)
- **F12. Funding rule plus the 3-hour balance lag (sections 7 and 8).** The 3-hour figure is correct ("a maximum of three hours for balances to appear"). But "unreserved available cash" has no formula. Without one, payouts sent after `last_refresh_time` are either subtracted twice or not at all. Section 2.3 gives the formula. The balances call also requires Transaction Search to be enabled on the app.
  - Links: [Transaction Search guide](https://developer.paypal.com/docs/transaction-search/) · [list balances](https://developer.paypal.com/api/transaction-search/v1/balances-get)
- **F15. Keys "derived from obligation id and attempt number" (section 7).** One item pays many obligations, so keys should derive from the line ID and attempt number. Also, PayPal's duplicate protection lasts only 30 days, so IDs must be globally unique (UUIDv7) to survive database resets.
- **F16. Partial refunds (section 5).** Offers and refunds can be partial (`dispute_outcome.amount_refunded`, `offer_amount`), but the obligation machine only has OPEN or VOID. Section 2.3 adds DISPUTE_LOST_PARTIAL with an ADJUSTMENT. The full `outcome_code` enum is not in the pages I read, so the mapping is filled only from recordings.
  - Link: [show dispute](https://developer.paypal.com/api/customer-disputes/v1/disputes-get)
- **F17. ACCEPT_CLAIM and funding.** "Merchants must have sufficient funds to issue a refund." The docs also conflict on `accept_claim_type` in the request body. Whether a held dispute is refunded from withheld or from available money is **[pending D10]**.
  - Link: [accept claim](https://developer.paypal.com/api/customer-disputes/v1/disputes-accept-claim)

**Low impact:**

- **F3.** REVERSED is "specific to payouts created through web uploads", so API payouts should never show it. Keep it in the enum but don't put it in the demo. [item statuses](https://developer.paypal.com/payouts/use-payouts/track-payout-item-status)
- **F14.** The duplicate-key error on simultaneous requests is documented, but its HTTP status isn't. Retention is "30 days" on the Payouts reference but up to 45 days on the general REST page; plan for 30. [API requests](https://developer.paypal.com/api/rest/requests/)

**Confirmed as written:**
- `sender_batch_id` reuse within 30 days is rejected with a link to the original.
- `PayPal-Request-Id` retries are safe.
- Only UNCLAIMED items can be cancelled, and unclaimed money auto-returns after 30 days.
- Provide evidence requires `WAITING_FOR_SELLER_RESPONSE` plus a link.
- Adjudicate requires UNDER_REVIEW, and require-evidence moves a dispute out of UNDER_REVIEW (sandbox only).
- `seller_response_due_date` exists, so the deadline guard can use it.
- The dispute webhook names are correct.
- The toolkit has no payout or provide-evidence tools.
- The demo arithmetic is internally consistent, *if* F4 and F6 hold in the sandbox.

---

## 4. Owner decisions (approved Oct 23)

1. **Rename reason** (verbatim for DECISIONS.md): "OnceGuard is the separate Airwallex project; CounterSign names this project's core principle: the AI proposes, the code countersigns."
2. **Evaluation baseline** runs only against `mock_paypal` with mock-only tools and never gets sandbox credentials. It stays fair by using PayPal's documented idempotency correctly. The report states all of this.
3. **LLM and adapter.** Use the toolkit's LangChain adapter behind the read-only allowlist. The model choice is deferred to core step 5. Keep the model swappable so the large evaluation can run on a cheap model.
4. **Payout lines** are added. Keys derive from `line_id` + `attempt_no`, and IDs are UUIDv7.
5. **F1 to F18 accepted as proposed**, including:
   - UNKNOWN is resolved only by an identical replay within 25 days, then goes to the owner;
   - cancel-before-replace checks GET for RETURNED;
   - disputes are opened via the sandbox Resolution Center and labelled staged;
   - pain 1 is reworded to "may freeze".

## 4a. Owner constraints

- **Timebox:** day one checks end on **Oct 25**. A BLOCKED check is recorded and left; do not chase it.
- **D9:** if evidence cannot be submitted at the inquiry stage, record which stage allows it. The demo will be adjusted.
- **D10, extra fallback:** test whether the buyer closing or cancelling the dispute in the Resolution Center resolves it and releases the funds. This is the fallback demo path if UNDER_REVIEW cannot be reached.
- **After day one, build a thin end-to-end vertical slice first.** One payout run with the dispute reserve, the write-ahead executor, the webhook inbox and reconciliation, working on the sandbox by **Nov 5**.
- **Deferred until the slice works:** the database transition triggers, the `allowed_transitions` table, and property-based tests.
- **From day one:** keep the import-linter contracts and the toolkit allowlist test.
- **Never read, print or log `.env` values.** Scripts load `.env` at runtime, and nothing echoes secrets, tokens or credentials.

## 4b. Execution order now

Step 0 (docs), then S1 to S3, then D1.

S1 (creating sandbox accounts) and S2 (REST app features) are manual steps in the PayPal developer dashboard, and the owner does them. I provide the exact checklist and the harness. S3: I write the webhook catcher, and the webhook can be registered by script through `POST /v1/notifications/webhooks` once a tunnel URL exists.

---

## 5. Verification

- **Day one:** every check has an evidence file under `recordings/sandbox/`. Every section 9 "verify" item has a verdict row in `SANDBOX_FINDINGS.md`. No secret or real email appears in a committed recording (a grep for the token, client ID and emails comes back empty in a pre-commit hook).
- **Schema:** `docker compose up postgres` plus `alembic upgrade head` apply cleanly. A test inserts a second live attempt for a line and expects a unique violation. Another inserts an unbalanced journal and expects the deferred trigger to fire.
- **State machines:**
  - In the vertical slice: example-based unit tests on the Python transition tables (legal moves pass, illegal moves raise), plus a CAS test where a stale version updates zero rows.
  - **After the vertical slice:** hypothesis property tests over random event sequences check that SETTLED is reached at most once per obligation unless a GET-confirmed clawback occurs, that there is never more than one live attempt per line, and that DISPUTE_EARMARK never changes cash.
  - **After the vertical slice:** the database transition trigger, plus a test asserting that the `allowed_transitions` rows equal the Python tables.
- **Hard rules:** import-linter contracts pass, and the toolkit allowlist test passes (it fails if `accept_dispute_claim` or any non-allowlisted tool appears).
- **Executor:** an integration test against `mock_paypal` drops the response after acceptance, then confirms that a replay with the same keys leaves exactly one item and that the obligation is SETTLED once.
