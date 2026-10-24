# C20: one engine interface for evaluation and the demo

## 1. What this adds

`countersign.harness` is the one core module that mate code imports. It exposes
`EngineSettings`, `Engine` and safe `EngineError` failures. The contract methods
reset and seed an evaluation database, receive webhooks, run one worker pass,
plan a payout run, list approvals, approve or reject a proposal, and read a snapshot.

The engine delegates to the existing approval, inbox, reconciliation and executor
services. It contains no second shield or payout protocol. `Executor.from_http`
constructs its own adapters inside the executor package, so the facade never
imports the write adapter. Cancel, evidence and accept-claim proposals are routed
to `Executor.execute`; C19b supplies their behavior, not this facade.

## 2. How one tick works

```mermaid
flowchart TD
    A[Engine.tick] --> B[Process one durable inbox delivery]
    B --> C[Reconcile fresh PayPal reads]
    C --> D[Select ALLOWED or APPROVED proposals]
    D --> E[Executor rechecks and executes]
    E --> F[Executor sweep recovers unresolved writes]
    F --> G[Return safe counts]
    H[Engine.snapshot] --> I[Read masked database views]
```

`tick()` returns exactly `processed_webhooks`, `proposals`, `operations` and
`errors`. Pending signature verification is not a processed delivery. Proposals
counts proposals routed successfully to execution. Operations counts newly created
operation IDs, not identical replay calls. Errors contains a count, never a
credential-bearing exception. Every stage gets a chance even when an earlier stage
fails. With `llm=None`, the agent stage is absent; non-null LLM settings fail with
`AGENT_NOT_IMPLEMENTED` until C23 supplies that service.

Tests use the actual facade and executor against the local mock. After a response
is deliberately dropped, the sweep reuses the original request; one PayPal item
exists, one obligation settles and one success journal is posted.

## 3. Seeding and reset safety

The eval caller seeds the PayPal mock separately. `Engine.seed()` receives only
`initial_state` with its `paypal` block removed. It creates makers, orders and
Money-derived order-share obligations, confirms supplied prior payouts and disputes
with fresh GETs, and reads balances. It never resets PayPal or calls mock controls.
All database seed changes commit together. A same-document rerun is a no-op;
different data or an unmarked nonempty marketplace is refused. Owner payees are
never overwritten by another seed.

Prior payouts need an explicit `order_keys` list whenever the maker has current
order shares. A list of order keys links the old payment to those shares. An empty
list means the historical payment is separate from current orders. Missing
ambiguous references return `SEED_PRIOR_LINK_REQUIRED` and roll back the seed.

Mate should add these references in the scenario fixtures; this card does not edit
mate-owned files:

| Fixture | Prior payout | Intended reference |
| --- | --- | --- |
| `demo_week.json` | `C-last-week` | `order_keys: []` |
| `F_retry_settled_obligation.json` | `B-settled` | `order_keys: ["o1051"]` |
| `F_webhook_forged.json` | `B-live` | `order_keys: ["o1051"]` |

`F_unclaimed_cancel_race.json` has no current orders for maker C, so its separate
historical payout is unambiguous; adding `order_keys: []` there is optional.

History retains actual GET-confirmed sender IDs and batch creation time. PayPal
does not expose the original `PayPal-Request-Id`. When not supplied explicitly,
the import records an `import-<uuid>` marker with `original_request_id_known=false`
and `import_only=true`. Its operation is already SUCCEEDED and is never queued
for a create or UNKNOWN replay. No original approval, proposal or response timestamp
is invented. Existing C15 code imports the confirmed financial state.

`reset()` first parses the real database name from the configured URL or conninfo.
Both it and the actual connected database must match and end in `_test` or `_eval`.
A query-string suffix cannot pass this check. Reset locks financial work, truncates
only the fixed application tables in the current schema, and uses no CASCADE.
Migration history, views and other schemas remain. The reset test creates and
drops its own randomly named test database; it never truncates the local dev database.

## 4. Snapshots and honest availability

`snapshot()` returns arrays named `incidents`, `proposals`, `runs`, `makers`,
`disputes`, `reservations` and `operations`, plus `cash`. Resource documents come
from the masked dashboard views under the financial lock. Operations expose only
IDs, kind, state and PayPal references, never private payloads or raw errors.
Historical run creation time comes from the confirmed imported operation.

Cash is null when no complete current observation is available or a refund
freshness guard is pending. An older complete view cannot replace that unknown
state. This is a database-backed facade snapshot; the HTTP dashboard itself still
uses fixtures until M15 and keeps its fixture-source header.

The integration tests reuse the mate's schema checker and schema documents, with
four explicit local corrections based on PLAN and CONTRACTS: `incident_id` can be
null, reservation foreign keys can be null, `record_versions` accepts arbitrary
record-ID keys, and `approval_request` exists only in NEEDS_OWNER. Other schema
constraints remain. Version-map values and state-dependent approval requests are
checked explicitly. Aman should apply these corrections to the fixture schemas;
this card does not change their ownership or the contract version.

## 5. Application workers and resources

The FastAPI lifespan now starts three independent loops: inbox processing,
reconciliation, and executor queue processing plus sweep. Defaults are conservative:

| Runtime variable | Default |
| --- | --- |
| `COUNTERSIGN_INBOX_INTERVAL_SECONDS` | 5 |
| `COUNTERSIGN_RECONCILE_INTERVAL_SECONDS` | 60 |
| `COUNTERSIGN_EXECUTOR_INTERVAL_SECONDS` | 60 |
| `COUNTERSIGN_WORKER_MAX_PROPOSALS` | 100 |

Intervals must be positive finite numbers; the proposal limit is 1 to 1000.
Variables are read at startup. Imports open no sockets and read no environment
files. Shutdown cancels pending worker I/O before closing owned resources; a write
interrupted after its durable preparation is recovered by the executor protocol.

Use `async with Engine(settings) as engine`, or call `await engine.aclose()` when
finished. Connections and transports supplied by a caller stay owned by that caller.
Settings and engine representations are masked. Runtime errors use fixed messages.

## 6. How this is tested

The tests cover public methods, real executor recovery, idempotent seed and rollback,
explicit historical order links, forged and pending signatures, durable ingress,
schema-shaped masked snapshots, unknown cash, reset refusal and isolation, worker
configuration, background startup and cancellation, and routing of unsupported
C19b actions. All network traffic is in-process mock traffic; `.env` is never read.

```powershell
$env:COUNTERSIGN_TEST_DATABASE_URL = 'postgresql://countersign:countersign-dev@127.0.0.1:55432/countersign'
.venv\Scripts\python.exe -m pytest tests/integration/test_harness.py -q
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\lint-imports.exe
```

The example password is the existing local-dev default. Database tests skip when
the test URL is unset. C09 recording validation and optional Toolkit checks keep
their existing skips. The walkthrough index remains Claude's responsibility.
