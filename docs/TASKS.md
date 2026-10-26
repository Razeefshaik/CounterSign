# CounterSign: Task board (Oct 23 to Nov 12, 2026)

**How to use this board:**
- Each card says who owns it, which files it may touch, and when it is done.
- Ownership rules are in `AGENTS.md`, and interfaces are in `docs/CONTRACTS.md`.
- A task may touch **only** the paths in its "Files" line. If it needs anything else, stop and say so.
- Every task runs on its own branch: `<owner>/<id>-<slug>`.
- "Needs the human owner" marks a step an agent cannot do, such as using the PayPal dashboard, logging in as the sandbox buyer, approving a shared file, or recording the video.

**Milestones:**
- **Oct 25:** day one checks done; `SANDBOX_FINDINGS.md` filled in.
- **Oct 26:** recordings published to mate; mate has worked without core until this date.
- **Nov 5:** vertical slice on the sandbox. One payout run with the dispute reserve, write-ahead executor, webhook inbox and reconciliation.
- **Nov 9:** demo week end to end on the sandbox, and the full evaluation done.
- **Nov 12, 12:00 PM PT (Nov 13, 1:30 AM IST):** submitted.

**Cut order if time runs short** (HANDOFF section 15): stretch B, stretch A, dashboard polish (M19, M20), then the size of the fault library (M14). **Never cut** C10 to C21, C23, M11 or M18.

---

## Core tasks

### Day one sandbox harness (`docs/PLAN.md` section 1)

**C01: Repo skeleton and guardrails**
- **Owner:** core. **Due:** Oct 23.
- **Goal:** a Python 3.12 project with lint, test and import-boundary tooling, so every later task lands on rails.
- **Files:** `pyproject.toml`, `LICENSE` (Apache-2.0), `README.md` (stub), `.gitignore` (all shared, so **needs the human owner's approval**); `src/countersign/__init__.py`; `tests/test_smoke.py`.
- **Depends on:** nothing.
- **Acceptance:**
  - `pip install -e .[dev]` works.
  - `ruff check`, `pytest` and `lint-imports` pass.
  - `pyproject.toml` defines the import-linter contracts from `docs/PLAN.md` section 2.1:
    - `countersign.agent` may not import `countersign.paypal.write`, `countersign.paypal.sandbox_controls`, `countersign.executor` or `countersign.db`;
    - only `countersign.executor` may import `countersign.paypal.write`.
  - Dev dependencies: `httpx`, `fastapi`, `uvicorn`, `python-dotenv`, `pytest`, `pytest-asyncio`, `ruff`, `import-linter`.
  - `.gitignore` also covers `recordings/**/raw/`.

**C02: HTTP client, recorder and redaction**
- **Owner:** core. **Due:** Oct 23.
- **Goal:** one sandbox HTTP client that every check uses. It records every exchange and never leaks secrets.
- **Files:** `scripts/sandbox_checks/{__init__,env,client,recorder,redact}.py`; `tests/sandbox_checks/test_recorder.py`, `tests/sandbox_checks/test_redact.py`.
- **Depends on:** C01.
- **Acceptance:**
  - `env.py` loads `.env` and exposes typed settings. It never prints values, and its `repr` masks them.
  - `client.py`:
    - gets the OAuth token and caches it until `expires_in`;
    - supports per-request timeout overrides;
    - supports `PayPal-Request-Id`;
    - handles multipart requests.
  - The recorder writes `recordings/sandbox/<date>/<check>/<NN>_<step>.json` with the fields in PLAN section 1.2. Raw, unredacted copies go only to `recordings/sandbox/raw/` (gitignored).
  - `redact.py` removes `Authorization`, access tokens, the client ID and the secret. It replaces every email from `.env` with its role alias (`maker-a@redacted`, ...).
  - Tests use fake env values and prove that no secret or email survives redaction.

**C03: Runner, manual steps, balance poller and the D1 check**
- **Owner:** core. **Due:** Oct 24.
- **Goal:** `python -m scripts.sandbox_checks run D1` works end to end.
- **Files:** `scripts/sandbox_checks/{__main__,runner,manual,poller}.py`, `scripts/sandbox_checks/checks/{__init__,d01_auth}.py`; `tests/sandbox_checks/test_runner.py`.
- **Depends on:** C02. **Needs the human owner:** S1 and S2 done, and `.env` filled in.
- **Acceptance:**
  - The runner executes the ordered steps of one check and resumes from the last completed step (state is kept in `recordings/sandbox/<date>/<check>/state.json`).
  - Manual steps print the instructions and wait for Enter and any IDs.
  - `poller.py` appends `GET /v1/reporting/balances` to `balances.jsonl` every 5 minutes while running.
  - D1 records the token's `scope` string, prints which of the required scopes are present (payouts, disputes read/update-seller, disputes/create, reporting), and writes a verdict line into a findings stub.

**C04: Webhook catcher and registration (S3)**
- **Owner:** core. **Due:** Oct 24.
- **Goal:** receive real sandbox webhooks with their exact raw bytes.
- **Files:** `scripts/sandbox_checks/{catcher,register_webhook}.py`; `tests/sandbox_checks/test_catcher.py`.
- **Depends on:** C02. **Needs the human owner:** start the tunnel (cloudflared or ngrok) and put `PAYPAL_WEBHOOK_ID` in `.env`.
- **Acceptance:**
  - `uvicorn scripts.sandbox_checks.catcher:app` stores the raw body bytes, headers and `received_at` per event under `recordings/sandbox/webhooks/`, and returns 200 within 1 second.
  - `register_webhook.py --url <tunnel>` subscribes to `PAYMENT.PAYOUTS-ITEM.*`, `PAYMENT.PAYOUTSBATCH.*`, `CUSTOMER.DISPUTE.*` and `PAYMENT.CAPTURE.*`, then prints only the new webhook ID.
  - A test posts a fake event and checks the stored bytes match exactly.

**C05: Payout checks D2 to D7**
- **Owner:** core. **Due:** Oct 24.
- **Goal:** automated payout and money-in checks as specified in PLAN section 1.3.
- **Files:** `scripts/sandbox_checks/checks/{d02_money_in,d03_payout,d04_unclaimed,d05_cancel,d06_idempotency,d07_negative}.py`; `tests/sandbox_checks/test_checks_payouts.py` (runs against a stub transport).
- **Depends on:** C03, C04.
- **Acceptance:**
  - Each check implements every sub-step of its PLAN section 1.3 card, with manual steps for buyer approval.
  - Keys use the `cs-<uuid7>-a<n>` format.
  - D6(e) uses a read timeout of about 50 ms.
  - The stub-transport tests prove step order and recording.

**C06: Dispute checks D8 to D10**
- **Owner:** core. **Due:** Oct 25.
- **Goal:** dispute creation, evidence and settlement checks, including the buyer-close fallback.
- **Files:** `scripts/sandbox_checks/checks/{d08_dispute_create,d09_evidence,d10_settle}.py`; `tests/sandbox_checks/test_checks_disputes.py`.
- **Depends on:** C03.
- **Acceptance:**
  - D8 tries the API route only if D1 found the `disputes/create` scope; otherwise it records BLOCKED. It guides the buyer through the Resolution Center steps and polls `GET /v1/customer/disputes` every minute.
  - D9 tries multipart first, then JSON, and records which stage accepted evidence.
  - D10 walks escalate, require-evidence, adjudicate and accept-claim, and the buyer close or cancel fallback.
  - Every `outcome_code` seen is written to `recordings/sandbox/outcome_codes.json`.

**C07: Analysis checks D11 to D13 and the toolkit allowlist test**
- **Owner:** core. **Due:** Oct 25.
- **Goal:** webhook timing and verification, balance freshness, and proof that the Agent Toolkit is read-only.
- **Files:** `scripts/sandbox_checks/checks/{d11_webhooks,d12_balances,d13_toolkit}.py`; `tests/unit/test_toolkit_allowlist.py`.
- **Depends on:** C04, C05.
- **Acceptance:**
  - D11 computes the delay, duplicates and order, and calls `verify-webhook-signature` on every stored event.
  - D12 derives, for each event type, the first balance snapshot that reflects it.
  - D13 installs `paypal-agent-toolkit==1.11.0`, builds the LangChain adapter with only `disputes.list/get`, `shipment.get`, `orders.get` and `transactions.list`, and asserts the exact tool set `{list_disputes, get_dispute, get_shipment_tracking, get_order_details, list_transactions}`.
  - `test_toolkit_allowlist.py` fails if `accept_dispute_claim` or any other tool appears, and runs in CI from now on.

**C08: Run day one and write up the findings**
- **Owner:** core. **Due:** Oct 25 (hard timebox).
- **Goal:** every section 9 "verify" item has a verdict.
- **Files:** `recordings/sandbox/**` (redacted only); `docs/SANDBOX_FINDINGS.md`, `docs/HANDOFF.md` section 9 and `docs/DECISIONS.md` (shared, so **needs the human owner's approval**).
- **Depends on:** C03 to C07. **Needs the human owner:** browser approvals and Resolution Center steps.
- **Acceptance:**
  - Every row in `SANDBOX_FINDINGS.md` has a verdict and an evidence file.
  - BLOCKED checks are recorded, not chased.
  - Every DIFFERENT verdict has a DECISIONS entry and a list of the [pending] items it resolves in CONTRACTS.md.
  - A grep for the client ID, token prefix and every `.env` email over `recordings/sandbox/` (excluding `raw/`) finds nothing.

**C09: Publish fixtures to mate**
- **Owner:** core. **Due:** Oct 26.
- **Goal:** mate can align `mock_paypal` with real payloads.
- **Files:** `recordings/sandbox/INDEX.md`; `docs/CONTRACTS.md` (resolve [pending] items, bump to v1.1; **needs the human owner's approval**).
- **Depends on:** C08.
- **Acceptance:**
  - `INDEX.md` maps each CONTRACTS.md endpoint and [pending Dn] tag to a recording file.
  - CONTRACTS.md v1.1 replaces each resolved [pending] default with the observed behaviour.

### Vertical slice (working on the sandbox by Nov 5)

**C10: Money type and state machines**
- **Owner:** core. **Due:** Oct 27.
- **Goal:** the pure-Python core from PLAN section 2.3.
- **Files:** `src/countersign/money.py`, `src/countersign/fsm/{obligation,line,attempt,operation,dispute,proposal,incident}.py`; `tests/unit/fsm/*`, `tests/unit/test_money.py`.
- **Depends on:** C01.
- **Acceptance:**
  - The transition tables match PLAN section 2.3. Illegal transitions raise `IllegalTransition`.
  - `WEBHOOK_TO_STATUS` and `OUTCOME_MAP` take the C09 findings into account.
  - Example-based tests cover every legal and illegal pair. (Property tests come in C22.)

**C11: Migrations, compose file and dashboard views**
- **Owner:** core. **Due:** Oct 28.
- **Goal:** the schema from PLAN section 2.2 (minus `allowed_transitions`), plus the `v_dash_*` views and `dash_money_graph` from CONTRACTS section 2.
- **Files:** `migrations/**`; `docker-compose.yml` (shared, so **needs the human owner's approval**); `tests/integration/test_schema.py`.
- **Depends on:** C10.
- **Acceptance:**
  - `docker compose up -d postgres && alembic upgrade head` succeeds.
  - Inserting a second live attempt for a line raises a unique violation.
  - An unbalanced ledger journal fails at commit.
  - Each view's `doc` validates against the CONTRACTS section 2 example shapes (JSON Schema tests).
  - A role `countersign_agent_ro` exists with SELECT on the views only.

**C12: Database layer**
- **Owner:** core. **Due:** Oct 28.
- **Files:** `src/countersign/db/**`, `src/countersign/config.py`; `tests/integration/db/*`.
- **Depends on:** C11.
- **Acceptance:**
  - An async pool and a `tx()` helper exist.
  - `cas_transition()` runs the CAS update and raises when zero rows change; a test shows a stale version updates nothing.
  - Repositories exist for every slice table.
  - UUIDv7 IDs are generated in the application.

**C13: PayPal adapter (read and write)**
- **Owner:** core. **Due:** Oct 29.
- **Files:** `src/countersign/paypal/{http,read,write,webhook_verify,sandbox_controls}.py`; `tests/contract/*`, which parse every C09 recording.
- **Depends on:** C09, C12.
- **Acceptance:**
  - `write.py` has `create_payout_single_item`, `cancel_item`, `provide_evidence` and `accept_claim`. Each takes an operation record and returns exactly one of `Ok(resp)`, `DefiniteError(resp)` or `Ambiguous(reason)`, classified from the C08 findings.
  - `read.py` has GET batch/item, list/get disputes, balances, trackers, captures and webhook events.
  - Contract tests pass against both the recordings and `mock_paypal` (once M02 has landed).
  - Import-linter still passes.

**C14: Webhook inbox and marketplace seed**
- **Owner:** core. **Due:** Oct 30.
- **Files:** `src/countersign/inbox/**`, `src/countersign/api/webhooks.py`, `src/countersign/main.py`, `src/countersign/worker.py`, `scripts/seed_marketplace.py`; `tests/integration/inbox/*`.
- **Depends on:** C13.
- **Acceptance:**
  - `POST /webhooks/paypal` stores the raw bytes in `webhook_inbox`, verifies the signature, and returns 200. A duplicate `event_id` is a no-op.
  - The processor GETs the resource before changing any state. A webhook never moves state on its own.
  - `CUSTOMER.DISPUTE.CREATED` on a known capture reserves the maker's share (`DISPUTE_EARMARK`) and moves the obligation to RESERVED in the same transaction.
  - `seed_marketplace.py` creates makers, orders and obligations from a YAML file.

**C15: Reconciliation poller**
- **Owner:** core. **Due:** Oct 31.
- **Files:** `src/countersign/reconcile/**`; `tests/integration/reconcile/*`.
- **Depends on:** C13, C14.
- **Acceptance:** on a schedule, the poller:
  - GETs every live attempt, every active dispute and the balances;
  - mirrors the states through the FSMs;
  - opens a `RECONCILIATION` incident on any unexpected transition;
  - backfills from `webhooks-events` if D11 found that endpoint works.

A test with webhooks dropped still reaches the correct final state.

**C16: Ledger, reservations and the cash formula**
- **Owner:** core. **Due:** Oct 31.
- **Files:** `src/countersign/ledger/**`; `tests/unit/ledger/*`, `tests/integration/ledger/*`.
- **Depends on:** C12.
- **Acceptance:**
  - Double-entry journals are written for payout submit, success, return and refund.
  - `cash.py` implements the PLAN section 2.3 formula.
  - Dispute earmarks never reduce `unreserved`.
  - The demo-week numbers from HANDOFF section 10 reproduce exactly ($1,340 available, $1,086 run, $254 unreserved).

**C17: Shield core**
- **Owner:** core. **Due:** Nov 1.
- **Files:** `src/countersign/shield/**`; `tests/unit/shield/*`.
- **Depends on:** C10, C16.
- **Acceptance:**
  - The slice rules are implemented as pure functions over a snapshot: SETTLEMENT, PAYOUT_STATE_MACHINE, CANCEL_BEFORE_REPLACE, IDEMPOTENCY, WRITE_AHEAD, DISPUTE_RESERVE, FUNDING, PAYEE_INTEGRITY, UNKNOWN_SCOPED, APPROVAL_BINDING.
  - The engine returns every rule outcome, including PASS.
  - There is one test per rule for each of PASS and BLOCK.
  - The "pay an already settled obligation" proposal is blocked by SETTLEMENT.

**C18: Approvals and owner endpoints**
- **Owner:** core. **Due:** Nov 2.
- **Files:** `src/countersign/approvals/**`, `src/countersign/api/{owner,runs}.py`; `tests/integration/approvals/*`.
- **Depends on:** C17.
- **Acceptance:**
  - The CONTRACTS section 3 endpoints work: approve, reject, plan run, set payee email.
  - Approvals bind the payload hash, record versions, expiry and single use.
  - A changed obligation version voids the approval with 409 `STALE_PROPOSAL`.
  - The payee email can change only through `POST /api/v1/makers/{id}/payout-email`.

**C19: Executor and UNKNOWN resolution**
- **Owner:** core. **Due:** Nov 3.
- **Files:** `src/countersign/executor/**`; `tests/integration/executor/*`.
- **Depends on:** C13, C17, C18.
- **Acceptance:**
  - The executor follows the PLAN section 2.3 protocol: lock, recheck, write-ahead commit, call, record.
  - UNKNOWN is resolved only by an identical replay within 25 days, then goes to the owner.
  - Against `mock_paypal` with `drop_response after_commit`: exactly one item exists in ground truth and the obligation is SETTLED once.
  - Two concurrent executors on the same line produce one attempt.

**C20: Engine facade**
- **Owner:** core. **Due:** Nov 4.
- **Goal:** the stable API from CONTRACTS section 4 for the eval and the demo bar.
- **Files:** `src/countersign/harness.py`; `tests/integration/test_harness.py`.
- **Depends on:** C14 to C19.
- **Acceptance:**
  - Every method in CONTRACTS section 4 works against `mock_paypal`.
  - `reset()` refuses any database whose name does not end in `_test` or `_eval`.
  - `snapshot()` returns shapes that validate against CONTRACTS section 2.

**C21: Vertical slice end to end (MILESTONE)**
- **Owner:** core. **Due:** Nov 5.
- **Files:** `scripts/slice_e2e.py`; `tests/integration/test_slice_mock.py`.
- **Depends on:** C20. **Needs the human owner:** open the sandbox dispute in the Resolution Center.
- **Acceptance:** on the **sandbox**:
  1. Seed two makers. One order is disputed through the Resolution Center (staged).
  2. Plan the run. The disputed share is reserved, not paid.
  3. The owner approves through the endpoint.
  4. The executor pays the other maker, with a forced lost response replayed safely.
  5. The webhook inbox and the poller agree.
  6. The incident closes only when the states reconcile.

The same flow passes as an automated test against `mock_paypal`. The sandbox resource IDs are logged for the video.

### After the slice

**C22: Deferred hardening**
- **Owner:** core. **Due:** Nov 7.
- **Files:** `migrations/**` (`allowed_transitions` table and trigger), `tests/property/*`.
- **Depends on:** C21.
- **Acceptance:**
  - The trigger rejects illegal state updates.
  - A test asserts the table rows equal the Python FSM tables.
  - Hypothesis property tests cover PLAN section 5: SETTLED at most once, at most one live attempt per line, and earmarks never change cash.

**C23: Agent tools and the Incident Commander**
- **Owner:** core. **Due:** Nov 7.
- **Files:** `src/countersign/agent/**`; `tests/unit/agent/*`.
- **Depends on:** C20. **Needs the human owner:** pick the model (swappable through config).
- **Acceptance:**
  - Tools come from the toolkit's LangChain adapter behind the exact allowlist, plus our own `get_payout_item`, `query_money_graph` (fixed queries, read-only role) and `simulate`.
  - The commander returns one structured proposal per step (HANDOFF section 6 schema).
  - Untrusted text is passed as data. An injection test produces a `PROMPT_INJECTION` flag and the shield blocks the action.
  - The startup assertion fails closed if the tool set differs.

**C24: Remaining shield rules, incidents and simulator**
- **Owner:** core. **Due:** Nov 8.
- **Files:** `src/countersign/{shield,incidents,simulator}/**`; tests.
- **Depends on:** C23.
- **Acceptance:**
  - REFUND_POLICY, EVIDENCE_INTEGRITY and DEADLINE_GUARD are implemented.
  - The incident lifecycle closes only on reconciliation.
  - The what-if simulator answers "pay run X with or without the reserve" and returns cash effects.
  - Autonomous PROVIDE_EVIDENCE works at the stage D9 found.

**C25: Demo week on the sandbox**
- **Owner:** core. **Due:** Nov 9.
- **Files:** `scripts/demo_week.py`.
- **Depends on:** C24. **Needs the human owner:** the staged buyer actions.
- **Acceptance:**
  - The HANDOFF section 10 week runs on the sandbox, including all three live safety moments.
  - It uses the D9 and D10 fallback paths where needed.
  - A run log lists every real sandbox resource ID.

**C26: Small sandbox integration evaluation**
- **Owner:** core. **Due:** Nov 9.
- **Files:** `eval/scenarios/sandbox_*.json` are mate-owned, so core only supplies resource IDs to mate. Core's own file is `scripts/sandbox_eval.py`.
- **Depends on:** C25, M11.
- **Acceptance:** about 5 sandbox scenarios run through the M11 runner, and the results are reported under `ground: "sandbox"` only.

**C27: Hosting**
- **Owner:** core. **Due:** Nov 10.
- **Files:** deploy config (shared, so **needs the human owner's approval**); `src/countersign/main.py`.
- **Depends on:** C21, M15.
- **Acceptance:**
  - A hosted URL serves the dashboard in demo mode against the bundled mock.
  - Live mode stays off on the hosted instance.
  - Secrets are in the platform's environment, never in the repo.

**C28: README, video and submission**
- **Owner:** core. **Due:** Nov 12.
- **Files:** `README.md`, `docs/**` (shared, so **needs the human owner**).
- **Depends on:** everything.
- **Acceptance:**
  - The README states what the AI does and what code does, the model used, the setup steps, the mock vs sandbox results, the claims language from HANDOFF section 12, and the platform-products note.
  - The licence is visible in the GitHub About section.
  - The video is under 3 minutes and labels the staged actions.
  - Submitted before Nov 12, 12:00 PM PT.

---

## Mate tasks

M01 to M08 depend only on `docs/CONTRACTS.md` v1.0, so mate never waits on core before Oct 26.

**M01: mock_paypal skeleton**
- **Owner:** mate. **Due:** Oct 24.
- **Files:** `mock_paypal/**` (app, store, clock, config); `tests/mock_paypal/*`.
- **Depends on:** CONTRACTS v1.0.
- **Acceptance:**
  - A FastAPI app on port 8099 provides:
    - `POST /v1/oauth2/token` and bearer-token checks;
    - the PayPal error envelope and the `paypal-debug-id` header;
    - `/_mock/reset`, `/_mock/clock/*` and `/_mock/config` with every flag default from CONTRACTS section 5.7.
  - All timers run off the mock clock.

**M02: Payouts endpoints and idempotency**
- **Owner:** mate. **Due:** Oct 25.
- **Files:** `mock_paypal/**`, `tests/mock_paypal/*`.
- **Depends on:** M01.
- **Acceptance:**
  - Create, GET batch, GET item and cancel work exactly as in CONTRACTS section 5.2.
  - The four idempotency rules work, including the concurrent duplicate (two `asyncio` requests give one 201 and one 409).
  - The 30-day key window works, and after it a reused key creates a new payout.
  - The item lifecycle and the balance effects behave as specified.
  - There is a test for each rule.

**M03: Fault injection and ground truth**
- **Owner:** mate. **Due:** Oct 26.
- **Files:** `mock_paypal/**`, `tests/mock_paypal/*`.
- **Depends on:** M02.
- **Acceptance:**
  - Every request action in CONTRACTS section 5.7 works, including `drop_response after_commit`: the item exists, but the client sees a dropped connection.
  - `test_value` reproduces the D7 codes.
  - Item transitions and claim (including `during_next_cancel`) work.
  - `GET /_mock/ground-truth` is complete.

**M04: Disputes, balances, orders, captures, tracking, transactions**
- **Owner:** mate. **Due:** Oct 26.
- **Files:** `mock_paypal/**`, `tests/mock_paypal/*`.
- **Depends on:** M01.
- **Acceptance:**
  - Every endpoint in CONTRACTS sections 5.3 and 5.4 works, with HATEOAS links that depend on status.
  - Holds move money between available and withheld.
  - Balance snapshots lag by `balance_refresh_seconds`.
  - The staged-dispute `/_mock` endpoints work, including `buyer-close`.

**M05: Webhook emission, signing and verification**
- **Owner:** mate. **Due:** Oct 27.
- **Files:** `mock_paypal/**`, `tests/mock_paypal/*`.
- **Depends on:** M02, M04.
- **Acceptance:**
  - Events are emitted with the CONTRACTS section 5.6 headers and signature scheme. A test verifies the signature with the cert from `/_mock/cert.pem`.
  - The verify endpoint returns SUCCESS for genuine events and FAILURE for forged ones.
  - The webhook faults (drop, duplicate, delay, reorder) and `forge` work.
  - Webhook registration, list and resend work.

**M06: Web dashboard shell and incidents grid (fixtures)**
- **Owner:** mate. **Due:** Oct 25.
- **Files:** `web/**`.
- **Depends on:** CONTRACTS v1.0.
- **Acceptance:**
  - A static app served from `web/` shows an AG Grid Community incidents table, fed by JSON fixtures copied from CONTRACTS section 2.
  - Filters by state and kind.
  - A demo/live mode badge from `/api/v1/meta`.
  - Works at phone width.
  - Staged items carry a visible "staged" label.

**M07: dashboard.py with the FixtureSource**
- **Owner:** mate. **Due:** Oct 25.
- **Files:** `src/countersign/api/dashboard.py`, `tests/dashboard/*`.
- **Depends on:** CONTRACTS v1.0.
- **Acceptance:**
  - Every GET endpoint in CONTRACTS section 2 is served from a `FixtureSource` that implements the `DashboardSource` protocol.
  - Pagination and filters work.
  - Tests validate each response against JSON Schemas derived from the CONTRACTS examples (kept in `tests/dashboard/schemas/`).

**M08: Scenario schema, validator and first scenarios**
- **Owner:** mate. **Due:** Oct 26.
- **Files:** `eval/scenario.schema.json`, `eval/scenarios/*.json`, `eval/validate.py`, `tests/eval/*`.
- **Depends on:** CONTRACTS v1.0.
- **Acceptance:**
  - The schema matches CONTRACTS section 6.1.
  - The validator checks references (maker keys, order keys, `at` offsets).
  - Six scenarios are written: `demo_week`, `F01_timeout_after_payout_created`, `F_unclaimed_cancel_race`, `F_webhook_forged`, `F_injection_refund_other_account`, `F_retry_settled_obligation`.

**M09: Align the mock with the recordings**
- **Owner:** mate. **Due:** Oct 28.
- **Files:** `mock_paypal/**`, `tests/mock_paypal/*`.
- **Depends on:** C09, M02 to M05.
- **Acceptance:**
  - The response shapes match the recordings in `recordings/sandbox/INDEX.md`.
  - Config defaults are updated to CONTRACTS v1.1.
  - Contract tests replay each recorded request against the mock and compare the response shape.

**M10: Dashboard views (fixtures)**
- **Owner:** mate. **Due:** Nov 1.
- **Files:** `web/**`.
- **Depends on:** M06, M07.
- **Acceptance:**
  - **Decision view:** proposal, rule results with PASS/BLOCK/NEEDS_OWNER, flags, and the approve and reject buttons calling the CONTRACTS section 3 endpoints. Approve sends `payload_hash`, and a 409 forces a reload.
  - **Payout run view:** lines, attempts and replacements.
  - **Execution history:** the audit log.
  - **Cash view:** freshness, the formula, and earmarks shown separately.
  - **Money graph:** an SVG view.

**M11: Eval runner, owner policies and metrics**
- **Owner:** mate. **Due:** Nov 1.
- **Files:** `eval/{runner,owner,metrics,report}.py`, `eval/systems/base.py`, `tests/eval/*`.
- **Depends on:** M03, M05, M08.
- **Acceptance:**
  - The runner executes the scenario timeline against the mock for any `System`.
  - The owner policies from CONTRACTS section 6.1 work.
  - The four invariants are computed from ground truth.
  - The runner writes `runs.jsonl`, `summary.json` and `report.md` exactly as in CONTRACTS section 6.3, with pass^k.
  - A dummy system that does nothing produces a valid report.

**M12: rules_only system**
- **Owner:** mate. **Due:** Nov 2.
- **Files:** `eval/systems/rules_only.py`, `tests/eval/*`.
- **Depends on:** M11.
- **Acceptance:** a deterministic workflow with no LLM that:
  - pays runs with correct idempotency;
  - cancels UNCLAIMED items;
  - reserves disputed shares by order ID;
  - uses keyword-only dispute triage.

It runs across all of M08's scenarios.

**M13: baseline_llm system (mock only)**
- **Owner:** mate. **Due:** Nov 4.
- **Files:** `eval/systems/baseline_llm.py`, `tests/eval/*`.
- **Depends on:** M11. **Needs the human owner:** a model key for the mock runs (cheap model).
- **Acceptance:**
  - A plain LLM agent with the CONTRACTS section 6.2 mock-only tools and documented idempotency handling.
  - It asserts that `mock_base_url` is localhost, and it is never given sandbox credentials.
  - The report states both of these.

**M14: Full fault library**
- **Owner:** mate. **Due:** Nov 4.
- **Files:** `eval/scenarios/*.json`.
- **Depends on:** M08, M09.
- **Acceptance:**
  - At least 2 scenarios for every `fault_class` in CONTRACTS section 6.1, plus 3 `UNSTRUCTURED_DISPUTE_TRIAGE` scenarios with realistic buyer text.
  - `provenance` is set honestly.
  - All scenarios pass the validator.

**M15: dashboard.py on the database**
- **Owner:** mate. **Due:** Nov 3.
- **Files:** `src/countersign/api/dashboard.py`, `tests/dashboard/*`.
- **Depends on:** C11, M07.
- **Acceptance:**
  - A `DbSource` reads the `v_dash_*` views and `dash_money_graph` through a read-only connection, selected by config.
  - The same schema tests pass against a seeded test database.

**M16: countersign eval system**
- **Owner:** mate. **Due:** Nov 6.
- **Files:** `eval/systems/countersign.py`, `tests/eval/*`.
- **Depends on:** C20, M11.
- **Acceptance:**
  - Wraps `countersign.harness.Engine` only.
  - Maps Engine approvals to `OwnerRequest`.
  - F01 passes, with zero invariant violations, on the mock.

**M17: Demo control bar**
- **Owner:** mate. **Due:** Nov 7.
- **Files:** `src/countersign/api/dashboard.py` (demo endpoints), `web/**`.
- **Depends on:** C20, M10, M11.
- **Acceptance:**
  - `GET /api/v1/demo/state`, `POST /api/v1/demo/step` and `POST /api/v1/demo/reset` play `demo_week.json` through the M11 runner and `Engine.tick()`.
  - The UI bar steps through the week, labels staged actions, and is never empty on first load.
  - While `replaying` is true, every dashboard view shows the persistent, non-dismissible banner `Simulated: mock PayPal`, taken from `simulation_label` (CONTRACTS section 3). It is visible at phone width, and a test checks it is present on each view in demo mode and absent in live mode.

**M18: Full evaluation**
- **Owner:** mate. **Due:** Nov 9.
- **Files:** `eval/results/**`, `eval/scenarios/*`.
- **Depends on:** M12, M13, M14, M16, C26.
- **Acceptance:**
  - Hundreds of mock runs (scenarios × 3 systems × k = 5) produce pass^k.
  - Mock and sandbox are reported separately.
  - Provenance is split.
  - `report.md` is ready for the README.

**M19: Naive vs CounterSign replay view**
- **Owner:** mate. **Due:** Nov 9.
- **Files:** `web/**`.
- **Depends on:** M18.
- **Acceptance:** a side-by-side replay of F01 and the settled-obligation scenario, showing baseline vs CounterSign actions and the ground-truth payouts, ready for the video hook.

**M20: Dashboard polish and hosting support**
- **Owner:** mate. **Due:** Nov 11.
- **Files:** `web/**`, `mock_paypal/**`.
- **Depends on:** C27.
- **Acceptance:**
  - The hosted demo mode works from a clean start.
  - Lighthouse accessibility is at least 90.
  - Phone width works.
  - There are no console errors.

---

## Calendar view

| Dates | Core | Mate |
| --- | --- | --- |
| Oct 23 to 25 | C01 to C08 (day one) | M01, M02, M06, M07 |
| Oct 26 to 28 | C09 to C12 | M03, M04, M05, M08, M09 |
| Oct 29 to Nov 2 | C13 to C18 | M10, M11, M12, M15 |
| Nov 3 to 5 | C19 to C21 (**slice by Nov 5**) | M13, M14 |
| Nov 6 to 9 | C22 to C26 | M16 to M19 |
| Nov 10 to 12 | C27, C28 | M20 |
