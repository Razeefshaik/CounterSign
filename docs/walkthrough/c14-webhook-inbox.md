# C14: receive first, verify, then confirm

## 1. What this adds

`POST /webhooks/paypal` records the exact received bytes in PostgreSQL. It returns
200 only after the transaction commits. A storage failure returns 503 so PayPal can
retry. The event id is unique: repeated delivery never overwrites the first event.
Malformed JSON returns 400. Only PayPal signature headers are saved; unrelated
Authorization and cookie headers are discarded.

The background worker verifies the saved bytes and signature headers, then reads
the resource from PayPal. There is no money-moving call in this path.

## 2. Why verification happens after acknowledgement

Acknowledgement means that the event is safely queued, not that its contents are
trusted. This keeps a verification outage from losing a delivery. SUCCESS permits
processing. UNKNOWN stays UNVERIFIED and pending for the next worker pass. An
explicit FAILURE is recorded as REJECTED and never processed.

The worker retries pending events every five seconds, ordering by attempt count
and reception time so an unavailable event does not permanently block later ones.
Errors store a fixed, safe message rather than a driver or PayPal exception.
Workers use row locks and the owner-decision advisory lock before money rows, so
two workers cannot process one event concurrently or race owner planning.

## 3. How a disputed order becomes protected

The worker GETs the dispute and checks its identity. It matches the capture to an
order using `seller_transaction_id`, the documented pending-D8b default. Tests or
the caller can supply another capture-field configuration once D8b confirms it.

In one database transaction the worker mirrors the dispute, creates its maker-share
earmark and changes OPEN or SCHEDULED obligations to RESERVED through the state
machine. For a $400 order and a 90 percent policy, that earmark is $360. It does
not reduce available cash again: `affects_cash` is false. Any failure rolls back
the mirror, earmark and obligation together. Repeated events reuse the earmark.

Disputes open an investigation incident. An already IN_FLIGHT or SETTLED obligation
is not falsely moved back to RESERVED. Resolved disputes with an unverified outcome
keep their reserve as UNKNOWN_OUTCOME. C15 and the pending C09 outcome mapping own
release, refund and payout reconciliation. Payout notifications are GET-confirmed
but make no payout or ledger changes in C14.

## 4. Starting the app

After setting process environment values described by C13, run:

```powershell
.venv\Scripts\python.exe -m countersign.main
```

The launcher selects the Windows Selector event loop required by psycopg, without
changing global policy when the module is imported. Settings are read during app startup.
Startup opens the database and HTTP client,
mounts the webhook and owner/run routes, and starts the inbox worker. Shutdown
stops the worker and closes owned connections.

The dashboard remains the mate's FixtureSource until M15. Its financial GETs show
example documents, not this database's new events or seed records. Every dashboard
GET response carries `X-CounterSign-Dashboard-Source: fixtures`, including in live
mode, so this limitation is visible over HTTP. Metadata comes
from the app settings and keeps the exact `Simulated: mock PayPal` label in demo
mode. This card does not claim a live database dashboard or a completed vertical
slice.

## 5. Seeding the local marketplace

```powershell
.venv\Scripts\python.exe scripts/seed_marketplace.py
```

The seed reads `eval/scenarios/demo_week.json`, not YAML. Its `initial_state` has
makers and orders but no obligations array, so one ORDER_SHARE obligation is
derived per order using Money.share_pct and the policy. Amounts round half up in
integer minor units. The due date is the Friday on or after the scenario start.
The demo seed creates three makers, four orders and four OPEN obligations.

A hash marker makes a repeat of the same document a no-op, preserving later owner
email changes and financial state. A different scenario or an unmarked nonempty
marketplace is refused. All seed writes commit together. The command prints
counts only. It does not import the mock's prior payouts, invent payout attempts,
set up the mock server, or pretend those payments happened in this database.

## 6. What the tests prove

The inbox integration tests use isolated PostgreSQL 16 schemas and fake network
responses. They cover committed exact bytes, concurrent duplicate delivery,
storage failure, forged signatures, verification retry, GET-before-mutation,
OPEN/SCHEDULED reservation, concurrent workers, foreign captures, rollback after
an earmark but before state change, unknown outcomes and safe seed reruns. They
make no external PayPal calls. Database cases skip when
`COUNTERSIGN_TEST_DATABASE_URL` is unset. The main app tests inject dependencies
and disable the background worker so they cannot start external traffic.
