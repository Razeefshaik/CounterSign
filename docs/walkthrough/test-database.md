# A disposable PostgreSQL database for local tests

Start the separate test service from the repository root:

```powershell
docker compose --profile test up -d postgres-test
$env:COUNTERSIGN_TEST_DATABASE_URL = 'postgresql://countersign:countersign-dev@127.0.0.1:55433/countersign'
$env:PYTHONPATH = 'src'
python -m pytest -q
```

This PostgreSQL 16 service is **for tests only**. Its data directory is a 512 MiB tmpfs mount with no persistent volume. It loses **all data on restart**. Crash-safe writes are disabled with `fsync=off`, `synchronous_commit=off` and `full_page_writes=off`. Do not use it for sandbox working state or recordings.

The profile is opt-in, and the port is bound only to `127.0.0.1:55433`. The existing `postgres` service and its persistent volume keep their original configuration and crash durability. The standard development URL remains on port 55432 in the current local setup.

To discard the test database and start empty:

```powershell
docker compose --profile test restart postgres-test
```

The database is deliberately named `countersign`, so test fixtures must use their own schemas; the facade's reset guard still rejects resetting this database. Tests specifically exercising reset create their own randomly named `_test` databases.

## Verification and comparison

The comparison uses the same 36-test selection on each URL, sequentially on the same checkout:

```powershell
python -m pytest -q tests/integration/test_schema.py tests/integration/test_c24_simulator.py tests/property/test_concurrent_executors.py
```

This covers migrations and downgrade, read-only roles and views, simulator cash/reserve behavior, and two real executors racing on a payout line. Hypothesis examples run inside their pytest case and are not counted as separate tests.

| Database | Scope | Result | Pytest time |
| --- | --- | --- | --- |
| Persistent service, port 55432 | Representative subset | 36 passed | 94.62 seconds |
| Disposable service, port 55433 | Identical subset | 36 passed | 31.89 seconds |
| Disposable service, port 55433 | Full suite | 2,265 passed, 6 skipped | 445.44 seconds (7m 25s) |

The measured subset is about 3 times faster on the disposable service. The prompt's representative-subset fallback avoids another long disk-backed full run; this table is not a comparison of two full-suite times. The full run skipped one unpublished sandbox-recording check and five optional toolkit checks, and reported one existing Starlette deprecation warning. Ruff passed, and import-linter kept all three contracts (zero broken).

Runtime checks confirmed the data directory is tmpfs, all three durability settings are off in `postgres-test`, and all three remain on in the existing development server. A probe table created in the disposable database disappeared after restarting only `postgres-test`.

## The simulator test flake

The clock was already fixed: `MockClock.now` changes only through explicit mock controls, and this test does not advance it. Repository snapshots already order their rows. The simulator reads stored balance timestamps in a repeatable-read, read-only transaction and contains no wall-clock reads.

The setup helper accepted a tick with zero errors as proof that reconciliation had fetched the new dispute. A busy reconciliation cycle can return zero errors without polling because another schema holds the database-wide advisory lock. The next line then indexed an empty dispute list.

Forcing two busy cycles reproduced that `IndexError` with the original fixture. Setup now waits, with a 60-second timeout, for the balance snapshot and the exact dispute mirror it needs. The test runs both ordinary and forced-busy setup, repeats both reserve-respecting and reserve-ignoring simulations with the fixed mock clock, checks identical output, and verifies the stored rows and clock remain unchanged. All 10 simulator tests passed after the fix. No production simulator or clock code changed.

The original one-off failure's traceback was not available. This reproducer establishes a setup race; it does not prove that race caused the historical failure or that its clock assertion failed.
