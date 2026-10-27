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
documentation sync `8402932` (current mergeable PR head). Validation:
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
[PR #12](https://github.com/Razeefshaik/CounterSign/pull/12) is open/ready with base
`mate/M14-fault-library`, head `489add7`. Merge PR #11, retarget #12 to main,
then merge M18 before C26; the stack is for review, not permission to merge.

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
[PR #13](https://github.com/Razeefshaik/CounterSign/pull/13) is open/ready, based on
`mate/M18-eval`, head `cfa862f` before the final context-only handoff commit.
Its branch is based on integration `a9a186d` with M18/M14 dependencies. Owner
merges #11, retargets #12 to main and merges, then retargets #13 to main and merges.
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
