# C22: database transition guards and generated safety tests

CounterSign already checks state changes in Python. C22 adds a second check in PostgreSQL so a direct SQL update cannot skip the same rules. It also generates event histories to test settlement, one-live-attempt and cash-reservation invariants.

## What the migration adds

Migration `0004_allowed_transitions.py` creates `allowed_transitions(entity, from_state, to_state)`. Its primary key covers all three columns. The 50 distinct pairs come from the actual obligation, line, attempt, operation, proposal and incident state machines. Different events reaching the same pair share one database row.

`migrations/generate_transitions.py` reads those machines and writes `migrations/sql/0004_allowed_transitions.json`. The revision inserts that checked-in output instead of importing changing application code when somebody applies an old migration. A unit test derives the expected rows independently from the Python tables and compares them with the frozen output. Database tests compare the installed rows too. A changed or missing transition is visible as drift.

Run `PYTHONPATH=src python migrations/generate_transitions.py --check` to check the snapshot. Before 0004 is published, the generator can regenerate it. Later state-machine changes need a new migration and corresponding drift-test updates; do not silently rewrite migration history.

One `BEFORE UPDATE OF state` trigger function serves six tables: `obligations`, `payout_lines`, `payout_attempts`, `operations`, `proposals` and `incidents`. A change absent from the table raises PostgreSQL check violation `23514`, naming `allowed_transitions`. The transaction retains its previous row. A same-state update passes. Inserts remain available for confirmed historical bootstrap records; this is an update guard, and the existing state-enum checks still apply. There is no payout-run FSM in C10, so payout_runs is outside this migration.

The trigger reads the transition table in the updated row's schema using `TG_TABLE_SCHEMA`. A different caller search path, including a shadowing temporary table, cannot select a more permissive allowlist. Downgrade removes all six triggers, the function and the table. A round-trip test verifies that upgrade restores enforcement.

```mermaid
flowchart TD
    A[SQL UPDATE of state] --> B{State unchanged?}
    B -->|Yes| P[Allow update]
    B -->|No| C{Attempt whose old state is neither PREPARED nor UNKNOWN?}
    C -->|Yes| M[Retain GET mirror; caller investigates unexpected observations]
    C -->|No| D{Pair exists in this schema's allowed_transitions?}
    D -->|Yes| P
    D -->|No| R[Reject update with check violation]
```

## Why payout attempts are different

PREPARED and UNKNOWN are CounterSign-owned attempt states. Their legal moves come from `attempt.OURS`. Once an attempt is SUBMITTED, its state mirrors a PayPal item GET. PayPal can report a surprising status, and PLAN says to retain it and open an incident. The transition trigger therefore checks only moves whose **old** attempt state is PREPARED or UNKNOWN. It does not turn the expected-mirror map into an external-state allowlist.

The one-live-attempt index remains separate and unchanged. It still prevents two live attempts for a line. If a surprising historical observation conflicts with that index, the existing reconciler preserves it in the raw resource mirror and opens an incident. C22 does not weaken that protection or invent new FSM transitions.

## What the generated tests exercise

The Hypothesis dependency is added only to the dev extras. Example counts are bounded, and database examples use rollback isolation inside the existing randomly named test schemas.

- **Settlement:** up to 200 random event sequences call the real obligation machine. Illegal events are rejected. Each interval between successful `PAYOUT_CLAWED_BACK` events contains at most one entry into SETTLED. An explicit sequence covers settlement, clawback and another settlement. The pure FSM receives the clawback event; existing application integration tests remain responsible for requiring GET confirmation before emitting it.
- **One live attempt:** up to 60 random prepare, submit, ambiguous, replay and GET-observation histories run through the actual attempt functions and PostgreSQL index. Repeated prepare attempts must raise the named unique violation while another attempt is live.
- **External mirrors:** up to 100 source/status combinations call the real `mirror` function and persist the result. Explicit surprising cases include SUCCESS to PENDING and a first SUBMITTED to REFUNDED observation.
- **Executor races:** eight bounded generated cases race two separate executor objects on the same approved line through the real mock transport. They check one attempt, one actual payout POST and one settled obligation, including repeated execute calls. Each example resets the mock and uses a fresh HTTP client because resetting the mock invalidates OAuth tokens.
- **Cash:** up to 200 random integer-money cases vary currencies, outflows, refresh boundaries and reservation lists. Earmarks change only the displayed earmarked total. RUN_CASH and PROPOSAL_CASH reduce free cash by exactly their sum. No inflow is invented and no hold is subtracted twice.
- **SQL bypass:** up to 120 illegal state pairs must fail at the database boundary without changing the row. All 50 generated legal pairs and same-state updates are tested separately. A temporary shadow allowlist cannot bypass the guard.

No real PayPal or model call is made. Hypothesis examples are not separate pytest test counts; the final report gives pytest counts and suite wall time separately.

## Files and running the checks

| Files | Purpose |
| --- | --- |
| `migrations/generate_transitions.py` | Generate or check the frozen state pairs |
| `migrations/sql/0004_allowed_transitions.json` | The reproducible revision input |
| `migrations/versions/0004_allowed_transitions.py` | Transition table, shared function, six triggers and downgrade |
| `tests/unit/fsm/test_transition_snapshot.py` | Frozen-row drift and mutation checks |
| `tests/property/conftest.py`, `hardening_world.py` | Reuse schema/harness fixtures and create constraint-valid test records |
| `tests/property/test_attempt_invariants.py` | Random histories, unique index and external mirrors |
| `tests/property/test_concurrent_executors.py` | Two real executors racing against the mock |
| `tests/property/test_money_and_settlement.py` | Settlement and integer-cash properties |
| `tests/property/test_transition_guards.py` | Direct SQL, schema isolation and downgrade/upgrade checks |
| `pyproject.toml` | Hypothesis in dev extras only |
| `tests/integration/test_harness.py` | Expect revision 0004 after reset |
| `tests/integration/executor/test_actions.py`, `tests/integration/test_c24.py` | Constraint-valid fixture setup and reconciliation mismatch injection |
| `docs/DECISIONS.md`, walkthrough index and this page | Design reasons and verification handoff |

Use the existing CounterSign-db `.venv`, set `PYTHONPATH=src` and point `COUNTERSIGN_TEST_DATABASE_URL` at a dedicated PostgreSQL 16 test database. The existing fixtures create `cs_test_<uuid>` schemas. Run `python -m pytest -q --tb=short -rs`, `ruff check .`, `lint-imports`, and the generator's `--check` command.

## Fixture repairs exposed by the guard

The first full run found three test-only shortcuts:

- The cancellation setup changed an obligation directly from OPEN to IN_FLIGHT. It now follows OPEN to SCHEDULED to IN_FLIGHT.
- The unapproved-claim test rewrote NEEDS_OWNER to ALLOWED. It now constructs the deliberately unapproved proposal using the legal CREATED to ALLOWED verdict path, and still verifies that execution is refused without owner approval.
- The C24 reconciliation test changed a SETTLED obligation back to IN_FLIGHT to inject a mismatch. It now injects and repairs a one-minor-unit amount mismatch, preserving the test's requirement that an unreconciled obligation prevents closure without manufacturing an illegal state or a fictional clawback.

The harness reset test also expects migration revision 0004. The guard and generated pairs were not changed to accommodate these fixtures.

C21's worktree and owned slice files are untouched. No application state machine has been edited. The full suite exposed no illegal application transition; the three shortcuts above were confined to tests.

## Verified results (October 25, 2026)

The final full suite ran against a dedicated, temporary PostgreSQL 16 database and the existing mock transport:

- `pytest -q --tb=short -rs`: **2,128 passed, 6 skipped, 0 failed**, with one existing Starlette/httpx deprecation warning, in **352.11 seconds (5 minutes 52 seconds)**.
- `ruff check .`: **0 issues**.
- `lint-imports`: **3 contracts kept, 0 broken**, analyzing 86 files and 284 dependencies.
- `python migrations/generate_transitions.py --check`: passed; the frozen snapshot contains **50 distinct pairs**.
- `git diff --check`: passed.

One skipped test requires C09's unpublished redacted sandbox recordings and index. The other five require the optional PayPal Agent Toolkit/LangChain packages. Those skips do not exercise live services. No real PayPal or model call was made.

The temporary test container, `countersign-c22-test-postgres`, was started with `--rm`. After verification, its stop command could not reach Docker Desktop's Linux-engine pipe, so container removal could not be confirmed. If it remains after Docker restarts, stop that named container; the shared development database was not touched.

There are no open implementation questions or known illegal application moves from this run. The branch starts at `4a057a4`; it has not been pushed or merged into main.
