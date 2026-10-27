# CounterSign agent state

Updated: Oct 27, 2026. This concise working summary does not replace AGENTS.md,
CLAUDE.md, HANDOFF.md, DECISIONS.md, PLAN.md, CONTRACTS.md or TASKS.md. Read the
required source documents and relevant future notes before changes. Context
keeper: **Professor Pickle**. Never store secrets, environment values, real emails
or raw model output here.

## Project

CounterSign coordinates marketplace maker payouts and buyer disputes. The model
reads and proposes; the deterministic shield and executor authorize and perform
financial writes. PostgreSQL holds state, reservations and the inbox. Money is
integer minor units. Mock and sandbox evidence must remain separate.

## Current work: C31 payout-run approval refresh

- Branch: `razeef/c31-run-approval-refresh`, isolated worktree
  `CounterSign-c31`, based on fresh `origin/main` `4a42bce` (merged M18 fix).
  Commits: `5b10c13` migration/schema, `29e7186` service/harness/regressions/decisions,
  `0b7cb62` contract 1.0.4 and owner-approved dashboard metadata alignment.
  Downgrade fix commit: `2ac9015`. Final full-suite validation passed;
  push is pending final verification. No PR or merge is requested.
- Migration 0006 permits CANCELLED history plus one live run per week through a
  partial unique index. `v_dash_runs` shows the live run instead of superseded
  cancelled history, preserving view grants/detail. Downgrade refuses conflicting
  historical duplicates without deleting records.
- Pending payout-run owner requests last 24 hours; other requests retain their
  existing 30-minute lifetime. After reconciliation, Engine tick refreshes stale
  or expired NEEDS_OWNER run requests under the owner-decision lock: expire the
  old proposal, release cash/allocations, cancel the run and replan the same week
  in one transaction. Known no-payable/shield refusals preserve the release and
  are audited. Approved and owner-rejected runs are untouched; manual planning
  can reuse a cancelled week. Shield/executor rules and eval grading are unchanged.
- CONTRACTS is 1.0.4 with the owner-authorized approve-409 refresh note and audit
  wording. Dashboard metadata reports 1.0.4; its one-line mate-owned edit was
  explicitly approved by the owner.
- Focused validation: service/schema/refresh selection 47 passed;
  executor/replacement/Engine selection 24 passed (overlapping selections; do not
  add these counts). Ruff clean, three import contracts kept, 39 web tests pass.
  `node --test web/tests` fails Node 24 directory resolution; the equivalent
  invocation with explicit test files passes.
- Five targeted model-disabled `run_one` probes on disposable migrated `_eval`
  databases completed with no errors and zero measured four-invariant counts.
  Before C31 all five failed and paid nobody. After C31:

  | Scenario | Result | Makers paid (USD) | Remaining completion gap |
  | --- | --- | --- | --- |
  | F_double_spend_overlapping_weeks | PASS | A 360, B 486 | None |
  | F_double_spend_same_week_planned_twice | PASS | A 360, B 486 | None |
  | F_double_spend_refund_and_run_pending | FAIL | A 360, B 486 | Expected no payments; missing refund/ACCEPT_CLAIM with model disabled |
  | F_stale_approval_dispute_opened | FAIL | B 486 only | Missing required RESERVE_EARNINGS |
  | F_stale_approval_partial_dispute | FAIL | A 360, B 486 | Missing required RESERVE_EARNINGS |

- Corrected probe evidence is temporary `countersign-c31-before.json` and
  `countersign-c31-after.json`; an earlier probe had a teardown error and is not
  the reported evidence. No full 480-cell rerun was performed. Passing payout
  behavior and zero invariant counts do not turn the three completion failures
  into passes or provide real-model/sandbox evidence.
- C25 real CLI mock run exited 0 with `python -m scripts.demo_week --target mock
  --model scripted`, loopback TCP mock/receiver and a fresh migrated `_test` DB.
  Database/ground-truth checks confirm Friday paid 108600 minor units, final
  available 354.00 USD and B's reserve 5400 minor units. Successful payout item
  amounts are 300, 300, 360 and 486 USD. Temporary CLI JSON/log evidence remains;
  the disposable database was cleaned up.
- Exact changed source/migration files: `src/countersign/approvals/service.py`,
  `src/countersign/harness.py`, `src/countersign/api/dashboard.py` (version only),
  `migrations/versions/0006_run_request_refresh.py`.
- Final full pytest: **3981 passed, 5 skipped in 1428.88 seconds**, exit 0.
  All 31 schema/new-migration checks also pass, including downgrade coverage.
  Ruff clean, three import contracts kept (zero broken), all 39 web tests pass
  with explicit file arguments. Final log/XML: temporary
  `countersign-c31-full-final.log` and matching XML. PostgreSQL WAL settings were
  restored to max 1024/min 80 MB after verification.
- Branch pushed to `origin/razeef/c31-run-approval-refresh`; validation notes
  committed in `0fee3ab`. No merge or PR was performed. The current interpreters
  are the original
  CounterSign worktree's `.venv/Scripts/python.exe` and
  `.venv/toolkit/Scripts/python.exe`; earlier temporary/worktree paths were
  superseded by owner cleanup. No real LLM/PayPal calls or `.env` reads.

## Continuation

Preserve task boundaries and existing work; stage explicit files only. Commit
messages are `C31: lowercase summary`, one line, without attribution. The task branch is pushed; do not merge. Any PR targets main. Refresh this state
with final exact results and commit references before handoff, and consult only
the relevant C31 section of [AGENT_FUTURE_NOTES.md](AGENT_FUTURE_NOTES.md).
