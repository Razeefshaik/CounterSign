# C11: the database keeps the money records consistent

## 1. What does this prove?

PostgreSQL 16 stores the marketplace's 19 tables from PLAN section 2.2. A payout
line can have only one live attempt. Every ledger journal must add up to zero in
each currency when its transaction commits. Dollars cannot balance out euros.

For example, a journal can add 100 cents to one account and subtract 100 cents
from another. Adding only the first posting temporarily is allowed inside the
transaction; committing that incomplete journal fails.

## 2. What do you do?

Start the local PostgreSQL service and provide `DATABASE_URL` in the terminal's
environment. Use a dedicated test database for integration tests. No PayPal
account or browser action is involved, and these commands do not load `.env`.

## 3. What does the code do?

Alembic applies numbered migrations containing raw SQL. Revision `0001` creates
the tables, the partial unique indexes and two ledger triggers. One trigger
serializes transactions writing the same journal. The deferred constraint
trigger checks the final totals, including after updates, deletes and moving a
posting to another journal. Each posting must name a journal, so an orphan
posting cannot bypass this check.

Money columns use integer minor units (`bigint`) and a three-character currency.
The `allowed_transitions` table and state-transition database triggers remain
deferred to C22; C10's Python machines and C12's compare-and-set update provide
the first state-transition guard.

## 4. What gets saved?

Database tables hold makers, orders, obligations, payout runs and lines,
operations and attempts, disputes, reservations, balanced ledger postings,
incidents, proposals, approvals, webhook events, balance snapshots, policies and
audit entries. Alembic also records which migration revision was applied.

The integration tests create separate temporary schemas and remove them after
each test. They never migrate the application's existing tables.

## 5. How is success decided?

Tests compare the table and CHECK lists with PLAN, verify money column types,
reject every kind of second live attempt, and verify that a terminal attempt
allows a later attempt. Ledger tests cover balanced and unbalanced journals,
different currencies, edits, deletion, moved postings and concurrent writers.
A downgrade test checks that the baseline objects can be removed.

Tests skip when `COUNTERSIGN_TEST_DATABASE_URL` is unset. A skipped test is not
evidence that a real PostgreSQL server accepted the migration.

## 6. How do you run it?

```powershell
docker compose --env-file NUL up -d postgres
python -m alembic -c migrations/alembic.ini upgrade head
python -m pytest tests/integration/test_schema.py
```

Set `DATABASE_URL` before the migration command, and
`COUNTERSIGN_TEST_DATABASE_URL` before running the database tests. Use the local
development credentials configured in compose or your own dedicated test
database. Do not copy real credentials into a committed file.

On Windows, `--env-file NUL` prevents Compose from reading `.env`. If port 5432
is reserved, set `$env:POSTGRES_PORT='55432'` before starting Compose and use that
port in both database URLs. The development test run used 127.0.0.1:55432.
