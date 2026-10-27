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
- Current branch: `mate/M14-fault-library`, based on upstream's M14 WIP
  `225d34b`, rebased onto that main. Rebased WIP commit: `4449c99`.
- M14 has 48 mock scenarios: at least two per fault class and three realistic
  dispute-triage cases. Independent rules_only run: 43 pass, five fail, zero
  runner errors and zero measured invariant violations. Provenance: 8 recorded,
  23 mixed, 17 synthesised. Walkthrough lists every case and known limitations.
- M14 completion fixes: FAILED webhook errors agree with subsequent GET; seeded
  FAILED items cannot claim recorded provenance; walkthrough completeness test.
  Full validation is in progress. No M14 PR yet.
- M18 orchestration and fake tests are prepared in uncommitted files; 17 fake
  tests pass. The actual model-disabled evaluation is running: all 48 scenarios,
  rules_only and countersign, k=5, 480 cells. Results are not final yet. No
  configured-model run by this agent.
- C26 implementation is being prepared separately; it must not weaken M11's
  mock-only guards or label mock results as sandbox observations.

## Working rules for continuation

Keep staged changes and commits scoped to the current task: future-task files
may be untracked while parallel agents prepare them. Never use `git add .`.
Before each PR, run required tests, Ruff and all three import contracts. Record
exact checks, commit hashes, limitations and PR links here after completion.
Do not edit web/ for this batch; the other owner is working there.

See [AGENT_FUTURE_NOTES.md](AGENT_FUTURE_NOTES.md) only for the related task's
details. Update this summary after each feature instead of carrying the whole
conversation into subsequent work.
