# C12: storing CounterSign records safely

## 1. What this adds

The database layer lets core code store makers, orders, payouts, disputes and the
other PLAN tables in PostgreSQL 16. It creates UUIDv7 IDs in the application and
provides a transaction helper and a state-change helper. It makes no PayPal calls.

## 2. What you (the owner) need

Start the C11 development database and apply its migrations first. Supply
`DATABASE_URL` in the process environment when starting the application. Importing
the settings module reads nothing; `load_settings()` reads it at runtime. Settings
and the database wrapper mask the connection string in their printed representation.
No file, including `.env`, is opened by this layer.

## 3. What the code does, step by step

1. Open `async with Database(load_settings()) as db` to start the connection pool.
2. Use `async with db.tx() as conn` for a transaction. Success commits; an exception
   rolls it back before the connection returns to the pool.
3. Get table repositories through `Repositories(conn)`, for example `.makers`.
   `insert`, `get`, `find`, `update` and `delete` use fixed table/column lists and
   bound values. `get(..., for_update=True)` can lock a row inside a transaction.
4. JSON columns use psycopg's JSONB adapter. Money columns accept integer minor units.
   Updates to versioned records increment their version; an optional
   `expected_version` rejects a stale update.
5. `cas_transition(conn, machine, table, row_id, expected_state, expected_version,
   event)` asks the supplied state machine first. An illegal event issues no SQL.
   The update then matches both state and version, or raises `StaleRow`.

## 4. What gets saved, and where

All 19 PLAN tables have repositories. UUID-keyed records receive UUIDv7 IDs unless
the caller supplies an ID. `payout_line_obligations` uses a tuple of line and
obligation IDs; policies and webhook inbox entries use their existing text keys.
Audit IDs come from PostgreSQL's sequence. Audit entries are append-only through
their repository. Ledger postings have no row key and support insert/find only.
No SQL, connection string or row contents are printed by this code.

## 5. How correctness is decided

Tests exercise real commits and rollbacks, JSONB, all table repositories, generated
IDs, composite/text/sequence keys, and a stale CAS that leaves state and version
unchanged. Separate safety tests check illegal transitions before SQL, identifier
validation, integer money, UUIDv7 clock regression and masked configuration.

PLAN only gives `state` and `version` together to obligations, payout runs, payout
lines and incidents, so CAS accepts those four tables. Attempts, operations and
proposals have no version column; disputes use `phase` rather than `state`. Their
repository methods remain available, but adding equivalent CAS support needs an
owner-approved schema decision. This implementation preserves the PLAN schema.

## 6. How to run it

Run safety checks without Docker:

```powershell
.venv\Scripts\python.exe -m pytest tests/integration/db/test_safety.py
```

Set `COUNTERSIGN_TEST_DATABASE_URL` in the terminal to a disposable PostgreSQL 16
database, then run `.venv\Scripts\python.exe -m pytest tests/integration/db`.
Each live test creates an isolated schema and removes it afterward. When the test
URL is unset, live tests skip and the safety tests still run. Never point these
integration tests at a production database.

The pool follows [psycopg's async pool lifecycle](https://www.psycopg.org/psycopg3/docs/advanced/pool.html).
On Windows, the application entry point must use a selector event loop, as
[psycopg requires](https://www.psycopg.org/psycopg3/docs/advanced/async.html);
the integration-test fixtures already select one.
