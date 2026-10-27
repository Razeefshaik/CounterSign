# CounterSign future task notes

Use only the section relevant to the current task. These notes are pointers,
not authority to override current repository instructions or contracts. No secrets.

## Context keeper workflow

Professor Pickle owns this note file and AGENT_STATE.md during delegated work.
At each completed feature, refresh the state with verified commits, PRs, actual
test results, unresolved blockers and the next task. Keep reusable implementation
details here under the matching task. When resuming, verify the short state
against Git, then read only relevant sections; do not load the whole conversation.
If no agent slot is available, the primary agent keeps the same workflow. The
persistent instruction is in AGENTS.md; no new user request is needed each time.

## PR workflow (owner prompt 07)

M14/M18/C26 integrated in main `0d47f64`; PR #11 is merged, and formerly
stacked #12/#13 are closed with `Merged into main in 0d47f64`. Every new PR
must target **main** and start from updated main; do not reuse the old stack.
Current fix branch is `mate/M18-eval-fix` from `2e51d41`.

## Local verification

- Windows main Python: `%TEMP%/countersign-m01-venv/Scripts/python.exe`.
- Isolated toolkit Python: `%TEMP%/countersign-m16-toolkit-venv/Scripts/python.exe`.
- Set COUNTERSIGN_TEST_DATABASE_URL to the documented disposable PostgreSQL test
  service on loopback port 55433, and COUNTERSIGN_TEST_TOOLKIT_PYTHON to the
  toolkit interpreter for every full test run. Do not read the project's .env.
- Docker Desktop is installed under `%LOCALAPPDATA%/Programs/DockerDesktop/`.
  Launch hidden if needed. Compose must use an explicitly empty temporary
  `--env-file` to avoid reading .env. Use the existing postgres-test service.
- Python asynchronous PostgreSQL needs SelectorEventLoop on Windows.
- Git remotes: origin is amanxsays/CounterSign; upstream is Razeefshaik/CounterSign.
  Fetch both; upstream/main is the integration source. Preserve local work.

## C31 payout-run approval refresh

Source: `prompts/codex/active/CODEX-PROMPT-C31-RUN-APPROVAL-REFRESH.md` in the
parent PayPalDevPost folder. Branch `razeef/c31-run-approval-refresh` starts from
`origin/main` `4a42bce`; commits use one-line `C31: lowercase summary` without
attribution. Commits: `5b10c13` migration/schema, `29e7186` core implementation,
`0b7cb62` contract 1.0.4 and owner-approved dashboard metadata, `2ac9015` downgrade
fix. Final full pytest: 3981 passed, 5 skipped in 1428.88 seconds; Ruff clean,
three import contracts kept and all 39 web tests pass. Branch push verified; no merge.

Migration 0006 replaces week-wide uniqueness with a unique non-CANCELLED run per
week. The dashboard run view must hide superseded cancelled history while keeping
the live run and its original grants/detail behavior. Downgrade must refuse
duplicate historical weeks without deleting records. Manual planning ignores
cancelled runs; owner rejection never schedules an automatic replacement.
In both frozen view definitions use only `p.params,p.created_at` in the proposals
lateral subquery. `SELECT *` binds the version column added by 0003 and blocks a
later downgrade through that migration. The first C31 full suite found this
existing downgrade-test failure (1 failed, 3980 passed, 5 skipped). Fix commit
`2ac9015` passes all schema/new-migration checks (31 passed in 18.88 seconds),
including the failed existing test. The repeated full suite passes (3981 passed,
5 skipped in 1428.88 seconds); this is the final full-suite evidence.

Refresh uses the same owner-decision lock as approve/reject and one transaction:
expire a stale/expired pending run proposal, release its PROPOSAL_CASH and current
allocations, cancel the old run, then replan from fresh context. Known planner
refusals use a savepoint so release/audit commit; unknown failures roll everything
back. Engine tick refreshes after reconciliation and before commander/executor.
Payout-run requests last 24 hours; other request lifetimes stay 30 minutes.

Five targeted model-disabled probes no longer strand payout-run approvals:
overlapping weeks and same-week-twice pass. Refund/run-pending still fails: it
pays A360/B486 against expected no payments and lacks refund/ACCEPT_CLAIM; the two
stale-approval scenarios pay the intended makers but lack RESERVE_EARNINGS.
Keep these completion failures visible. Corrected temporary before/after JSON
replaces an invalid earlier teardown probe. Do not rerun the 480-cell evaluation
or change grading/scenarios; Aman owns the next full comparison.

Verification requires explicit DB/toolkit settings and Windows SelectorEventLoop.
The verified CLI `python -m scripts.demo_week --target mock --model scripted`
exits 0: Friday pays 1086 USD once, following-week A360 leaves354 available,
and B54 remains earmarked. It uses a loopback TCP mock/receiver and disposable
migrated `_test` DB. Node 24 cannot resolve the test directory via
`node --test web/tests`; explicit test-file arguments pass all 39 web tests.
The current main/toolkit interpreters are in the original CounterSign worktree's
`.venv/Scripts/python.exe` and `.venv/toolkit/Scripts/python.exe` after owner
cleanup superseded earlier paths. Record final counts/push only after completion.
No real model/PayPal calls and no `.env` reads.

## M14 fault library

Source prompt: Downloads/AMAN-PROMPT-05-M14-M18-EVAL.md. Walkthrough:
docs/walkthrough/m14-fault-library.md. Ground truth has four named invariants;
disputed-share detection bounds maker totals and cannot identify allocations
inside an otherwise permitted partial payout. Zero counters on error rows do not
certify safety. Request delay/timeout faults wait on mock time, so library lost
responses use drop/reset/after-commit HTTP errors instead of blocking a tick.
The new /_mock/payout-outcomes control is proposed for CONTRACTS section 5.7;
do not edit CONTRACTS without owner approval.
Completed M14 is PR #11; commits `4449c99`, `c2c4fca`, `fc65de0`, plus integration
documentation sync `8402932` (historical review head). Integrated in main
`0d47f64`. Validation:
3,883 passed, five legacy optional-toolkit skips; Ruff and three import contracts
pass. The 43/48 rules_only score is a benchmark result, not a failing test suite.

## M18 evaluation

Implementation: eval/evaluate.py. Run `python -m eval.evaluate --model none --k 5
--output eval/results/2026-10-27-model-none` with documented test DB settings.
It creates, migrates and drops its own generated _eval database. `--model none`
runs rules_only and model-disabled CounterSign; it does not measure AI reasoning.
Configured mode is for Razeef: same explicit Gemini model for baseline_llm and
CounterSign, plus rules_only; TCP mock supports the genuine toolkit subprocess.
No .env read. Quota stops preserve completed cells, retry the interrupted cell
on `--resume`, and emit no completed summary until every cell finishes.
The manifest binds corpus hashes, k, model and code hashes. Keep evaluated source
files stable while a run is active. Stopped-attempt usage is separate from scored
cells. Proposed CONTRACTS 6.2/6.3 wording is in the M18 walkthrough.
The source fingerprint covers the benchmark, system adapters, core, mock and
migrations plus relevant JSON fixtures; it excludes independent C26 preparation
and result artifacts. A superseded pre-fingerprint run was stopped and archived
outside the repository; only the final run should be committed as evidence.
The final completed run evaluates source commit `e2f5687`; results are committed
in `f9e8dfd`. The later unused-noqa cleanup was AST-verified non-behavioural;
the result README preserves the actual source attribution. The early attempt
had more than 123 completed cells; its task-owned database was removed and its
checkpoint is archived outside the repository, never scored as the final run.
The final fingerprinted run later stopped at 410 completed cells with the CLI's
fixed generic error (no raw cause published). Its exact identity checkpoint
accepted `--resume` and completed the remaining 70 cells, retaining all previous
cells. The cause is unknown; do not describe this model-disabled stop as quota.
Final 480-cell results: rules_only 215/240 pass (pass^5 43/48), zero errors,
260 owner interruptions; CounterSign 80/240 pass (pass^5 16/48), 80
EVALUATION_FAILED rows, 200 owner interruptions. Both have zero model calls,
tokens and measured invariant counts. Error-row zeros are not safety evidence.
M18 full pytest: 3,900 passed, five legacy optional-toolkit skips (712 seconds);
Ruff and three import contracts pass. Context is committed in `23d0e11`, current
integration documentation in `ef00286`, handoff in `489add7`.
[PR #12](https://github.com/Razeefshaik/CounterSign/pull/12) was formerly based on
M14; it is now closed because the work is integrated in main `0d47f64`. All
future PRs target main. The former review stack is obsolete.

## M18 error-row repair (prompt 07; active)

Source prompt: Downloads/AMAN-PROMPT-07-EVAL-FIX.md. Diagnose, then narrow the
adapter classification: only fixed, proven no-write planner refusals such as
NO_PAYABLE_OBLIGATIONS, RUN_ALREADY_EXISTS or SHIELD_BLOCKED may be recorded as
blocked actions. Unknown EngineError codes must remain failures. Never store raw
exception text. Match expected tick conditions by fixed codes/states per stage;
do not suppress an arbitrary error count. Fix real core/mock mismatches instead,
with a regression test and an explicit list of core files in the PR.
The prompt's group counts are approximate; verify actual cases rather than
assuming its seven/nine headings equal the listed scenarios. Keep model-disabled
failures without errors visible; no model means agent actions are not proposed.
Code commit `c24783a` implements exactly the three planner codes above; all
unknown Engine/runtime errors still raise. The only core file touched is
`src/countersign/harness.py`: loopback mock HTTP token expiry uses the supplied
mock clock, while sandbox retains real monotonic time. No blanket tick-error
suppression was added; the persistent-tick-error regression remains.
Before: all eight group-B cases had attempt/dispute/balance GET failures; the
legacy-clock control for F_blocked_new_payout_not_retried reproduced nine 401s
and EVALUATION_FAILED. After: all 16 former-error scenario regressions pass
(66 seconds), with error=None, zero reconciliation-stage errors and no 401s;
all eight long cases refresh OAuth at least twice. Eighteen adapter checks and
two mock/sandbox clock-construction tests pass. Safe diagnostics are in the
temporary `countersign-m18-diagnose.log`; no private exception arguments.
The corrected 480-cell run is complete on source `c24783a`, with artifacts in
`45272b0` under `eval/results/2026-10-27-model-none-fixed/`. Its exact 48 scenario
hashes, two systems and k=5 match the old run; the old folder is unchanged.
CounterSign improves from 80/240 pass (33.3%) with 80 errors to 115/240 (47.9%)
with zero errors, pass^5 23/48. rules_only stays 215/240 (89.6%) with zero errors.
Both have zero measured four-invariant counts, calls and tokens. All 25 remaining
model-disabled CounterSign completion failures stay visible and are listed in
the new README/walkthrough; no agent actions or expected outcomes were invented.
Privacy, experiment identity and unique-cell count checks pass. Ruff, three
import contracts and 39 web tests pass. Full pytest is still running in
`countersign-m18fix-full.log`; no full-suite completion or PR number is claimed.
Next: record full checks, push and open one main-based PR listing the core change.
No real LLM/PayPal calls; no .env reads.

## C26 sandbox preparation

Source prompt: Downloads/AMAN-PROMPT-06-C26-SANDBOX-EVAL.md. Core scripts/ and
minimal src/ changes are owner-authorized for C26 only. No real sandbox calls
by this agent; Razeef runs them later. Only CounterSign, no baselines.
M11 run_one and CountersignSystem deliberately refuse sandbox ground; do not
weaken those guards or relabel rows. An observed-only integration entry point
must report unavailable invariants explicitly instead of inventing zero values.
Reuse Engine, scripts.slice_e2e.Checkpoint, LostResponse, ResourceLog and
scripts.sandbox_checks Relay/ManualSteps. Recover interrupted operations from
GET-confirmed Engine state before side effects. Never save raw snapshots.
Live mode must require explicit sandbox target, COUNTERSIGN_MODE=live, exact
sandbox origin, dedicated database and matching webhook tunnel. Capture/order
setup is owner-operated; financial writes remain shield/executor-only.
Required cases: registered payout, unregistered cancel confirmed RETURNED,
lost-response duplicate-link recovery, capture PENDING with maker-share reserve,
and an actual SETTLEMENT/BLOCK proposal after settlement (not just duplicate-week
RUN_ALREADY_EXISTS). Mock dry-run exercises the same integration path.
The exact CLI mock dry-run completed all five checks twice with ground mock;
output is under `%TEMP%/countersign-c26-dry-run-20261027`. Seventeen focused
tests pass; all 53 scenario JSON files validate (48 mock, five sandbox). First
commit `b7c715c` includes eval/sandbox.py, the M11 integration wrapper, script,
five scenario catalogue files, mock CLI filtering, tests, runbook and decisions.
The only core-owned file touched is `scripts/sandbox_eval.py`; no src/ changes.
Full C26 pytest: 3,922 passed, five legacy toolkit skips (709 seconds); all
17 focused and 31 web tests pass. Ruff, three import contracts and diff checks
pass. `cfa862f` records validation/runbook completion.
[PR #13](https://github.com/Razeefshaik/CounterSign/pull/13) is closed because
the work is integrated in main `0d47f64`. Its former base was M18; all future
PRs target main, and the old review-stack instructions no longer apply.
No agent merge or real sandbox/model evaluation occurred. Razeef's configured
Gemini and actual sandbox runs use the exact owner commands in the M18/C26
walkthroughs; those future runs are not blockers to completed preparation.

## Hosting/dashboard follow-ups

M15 default source is fixtures; hosting must select database. Separate read-only
pool uses countersign_agent_ro and the v_dash_* views. Shared test module loading
belongs in dashboard_support.py, never `from conftest import dashboard`.
The cash view versus Engine.snapshot availability guards were handed to core.
Mock snapshot history is bounded at 256 entries; evicted historical reads return
404. Neither note permits rebuilding completed M15/M17 work.

Final verification: full pytest **3,963 passed, five legacy toolkit skips**;
Ruff clean, all three import contracts kept, all 39 web tests pass.
All 480 corrected cells completed with zero errors. PR targets main.

Completed prompt07: PR #14 (https://github.com/Razeefshaik/CounterSign/pull/14) is open and ready,
base main; code c24783a, results45272b0, final validation b9d2a04.
The earlier pending/progress notes above are historical; full validation passed.
No merge performed. Next: owner review, then configured-model comparison.
