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

M14 fault library, M18 evaluation preparation/model-disabled run, and C26 sandbox
evaluation preparation are complete for the authorized scope. Each has its own
branch and open, ready PR. No PR was merged and no real PayPal/LLM call was made.
Configured Gemini and actual sandbox execution remain owner-operated by Razeef;
they are next steps, not blockers to this preparation handoff.

- Latest integration base: `a9a186d`; M13, M15 and M17 are already integrated.
- Current branch: `mate/C26-sandbox-eval`, stacked on M18/M14 dependencies.
  Owner review order: merge #11, retarget #12 to main and merge, then retarget
  #13 to main and merge. The agent does not merge them.
- M14 has 48 mock scenarios: at least two per fault class and three realistic
  dispute-triage cases. Independent rules_only run: 43 pass, five fail, zero
  runner errors and zero measured invariant violations. Provenance: 8 recorded,
  23 mixed, 17 synthesised. Walkthrough lists every case and known limitations.
- M14 completion fixes: FAILED webhook errors agree with subsequent GET; seeded
  FAILED items cannot claim recorded provenance; walkthrough completeness test.
  Complete: [PR #11](https://github.com/Razeefshaik/CounterSign/pull/11) is open,
  ready for review, not merged. Full suite: 3,883 passed, five legacy toolkit
  skips. Ruff and three import contracts pass. Current PR head: `8402932`
  (integration documentation sync); GitHub reports it mergeable.
- M18: `e2f5687` orchestration; `f9e8dfd` persists all 480 model-disabled cells
  (48 scenarios x two systems x k=5), evaluated on `e2f5687`. rules_only: 215/240
  pass, pass^5 43/48, zero errors, 260 owner interruptions. CounterSign: 80/240
  pass, pass^5 16/48, 80 EVALUATION_FAILED rows, 200 owner interruptions. Both
  have zero measured four-invariant counts, calls and tokens; error-row zeros do
  not establish safety. M18 full suite: 3,900 passed, five legacy toolkit skips;
  Ruff and three import contracts pass. Additional commits: `23d0e11` (context),
  `ef00286` (integration docs), `489add7` (handoff). [PR #12](https://github.com/Razeefshaik/CounterSign/pull/12)
  is open and ready against `mate/M14-fault-library` for stacked review.
- C26: `b7c715c` implementation, `cfa862f` validation/runbook. [PR #13](https://github.com/Razeefshaik/CounterSign/pull/13)
  is open/ready against `mate/M18-eval`, not merged. Full suite: 3,922 passed,
  five legacy toolkit skips; 17 focused and 31 web tests pass. All 53 scenarios
  validate (48 mock, five sandbox); exact CLI mock dry-run passed five checks
  twice. Ruff, three import contracts and diff checks pass. Only final context
  notes await their handoff commit; do not guess that commit's hash.

## Working rules for continuation

Keep staged changes and commits scoped to the current task: future-task files
may be untracked while parallel agents prepare them. Never use `git add .`.
Before each PR, run required tests, Ruff and all three import contracts. Record
exact checks, commit hashes, limitations and PR links here after completion.
Next: owner reviews the PR stack, then runs the exact configured-model and
sandbox commands in the M18/C26 walkthroughs. Do not edit web/ for this batch.
The context workflow and prior summaries are committed; refresh these notes at
the next verified PR/feature boundary without assuming pending checks passed.

See [AGENT_FUTURE_NOTES.md](AGENT_FUTURE_NOTES.md) only for the related task's
details. Update this summary after each feature instead of carrying the whole
conversation into subsequent work.
