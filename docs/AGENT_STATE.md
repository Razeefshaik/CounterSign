# CounterSign agent state

Updated: Oct 27, 2026. This is a concise working summary, not a replacement for
AGENTS.md, CLAUDE.md, HANDOFF.md, DECISIONS.md, PLAN.md, CONTRACTS.md or TASKS.md.
Read the required source documents and the relevant task notes before changes.
Never put credentials, environment values, real emails or raw model text here.
Context keeper: **Professor Pickle**. Update this file after every feature and
before handoff; consult only the related future-note sections for the next task.

## Project

CounterSign coordinates marketplace maker payouts and buyer disputes. The model
reads and proposes; the deterministic shield and executor authorize and perform
financial writes. PostgreSQL holds application state, reservations and the inbox.
The local PayPal mock supports fault evaluation; mock and sandbox evidence must
remain separate. Money is integer minor units, with decimal strings in API payloads.

## Current work

The owner requested three tasks in order using the downloaded prompt 05 and 06:
M14 fault library, M18 full evaluation, then C26 sandbox evaluation preparation.
One branch and PR per task. Never merge. No real PayPal or LLM calls for these tasks.
Task reviews and implementation were delegated in batches; Professor Pickle
maintains these context files while the primary agent owns feature completion.

- Latest fetched main: `dc24c7f`; M13, M15 and M17 are already integrated there.
- Current branch: `mate/M18-eval`, on top of M14. M14 commits: `4449c99`
  (rebased WIP), `c2c4fca` (coverage/failure evidence), `fc65de0` (context workflow).
- M14 has 48 mock scenarios: at least two per fault class and three realistic
  dispute-triage cases. Independent rules_only run: 43 pass, five fail, zero
  runner errors and zero measured invariant violations. Provenance: 8 recorded,
  23 mixed, 17 synthesised. Walkthrough lists every case and known limitations.
- M14 completion fixes: FAILED webhook errors agree with subsequent GET; seeded
  FAILED items cannot claim recorded provenance; walkthrough completeness test.
  Complete: [PR #11](https://github.com/Razeefshaik/CounterSign/pull/11) is open,
  ready for review, not merged. Full suite: 3,883 passed, five legacy toolkit
  skips. Ruff and all three import contracts pass in the isolated M14 checkout.
- M18: `e2f5687` orchestration; `f9e8dfd` persists all 480 model-disabled cells
  (48 scenarios x two systems x k=5), evaluated on `e2f5687`. rules_only: 215/240
  pass, pass^5 43/48, zero errors, 260 owner interruptions. CounterSign: 80/240
  pass, pass^5 16/48, 80 EVALUATION_FAILED rows, 200 owner interruptions. Both
  have zero measured four-invariant counts, calls and tokens; error-row zeros do
  not establish safety. Full M18 pytest is running; 17 fake tests pass. No PR yet.
- C26 is uncommitted preparation; 12 fake/real-Engine mock dry-run tests pass.
  The CLI mock dry-run completed all five checks, labelled mock. Scenario JSON
  files remain held. No real model/sandbox calls were made for this batch.

## Working rules for continuation

Keep staged changes and commits scoped to the current task: future-task files
may be untracked while parallel agents prepare them. Never use `git add .`.
Before each PR, run required tests, Ruff and all three import contracts. Record
exact checks, commit hashes, limitations and PR links here after completion.
Do not edit web/ for this batch; the other owner is working there.
Context-file updates after `fc65de0` are uncommitted and belong with M18's handoff.

See [AGENT_FUTURE_NOTES.md](AGENT_FUTURE_NOTES.md) only for the related task's
details. Update this summary after each feature instead of carrying the whole
conversation into subsequent work.
