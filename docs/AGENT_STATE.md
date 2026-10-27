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

- Active task: prompt 07, repair M18's 80 model-disabled CounterSign error rows,
  then rerun the same 48 scenarios x two systems x k=5 into a **new** results
  folder. Preserve the existing experiment and report honest before/after scores.
- Current branch: `mate/M18-eval-fix`, created from updated main `2e51d41`.
  Future PRs must target **main**, never a task branch. No new PR yet.
- M14, M18 and C26 are integrated into main in `0d47f64`. PR #11 is merged;
  formerly stacked PRs [#12](https://github.com/Razeefshaik/CounterSign/pull/12)
  and [#13](https://github.com/Razeefshaik/CounterSign/pull/13) were closed with
  the exact comment `Merged into main in 0d47f64`. Old stack instructions are obsolete.
- Code commit: `c24783a`. Only NO_PAYABLE_OBLIGATIONS, RUN_ALREADY_EXISTS and
  SHIELD_BLOCKED planner refusals become blocked actions; unknown errors still fail.
  The sole core change is `src/countersign/harness.py`: loopback mock OAuth expiry
  uses the supplied mock clock. Sandbox's real monotonic clock is unchanged.
- Eight group-B diagnostics found GET failures from expired mock tokens; the
  legacy clock control reproduced nine HTTP 401s and EVALUATION_FAILED. After
  the fix, all 16 former-error scenario regressions pass with error=None,
  zero reconciliation-stage errors and no 401s. The eight long cases refresh
  OAuth at least twice. Eighteen adapter and two clock-construction checks pass.
  Persistent tick errors still fail; no blanket suppression was added.
- All 480 corrected-run cells are complete on `c24783a`, persisted in `45272b0`
  under `eval/results/2026-10-27-model-none-fixed/`. The old report is untouched;
  all 48 scenario hashes, two systems and k=5 match exactly.
- CounterSign: 80/240 pass (33.3%), 80 errors before; 115/240 pass (47.9%),
  zero errors after (pass^5 23/48). rules_only remains 215/240 (89.6%), zero
  errors. Both have zero measured four-invariant counts, model calls and tokens.
  CounterSign's 25 model-disabled completion failures remain visible; this is
  not real-model evidence or proof of safety.
- Privacy, identity and 480 unique cells are checked. Ruff, three import contracts
  and 39 web tests pass. Full pytest (`countersign-m18fix-full.log`) is still
  running without reported failures so far; no new PR or full-suite claim yet.
- Model-disabled triage/injection/deadline/cancellation gaps remain visible;
  do not invent agent actions or change expected outcomes to improve scores.
- Previous validation: M14 3,883 passed, M18 3,900 passed, C26 3,922 passed,
  each with five legacy toolkit skips. These counts do not validate the new fix.
  C26 mock dry-run completed five checks; actual sandbox/model runs are not part
  of this task. Never read .env or call real model/PayPal APIs.

## Working rules for continuation

Keep staged changes and commits scoped to the current task: future-task files
may be untracked while parallel agents prepare them. Never use `git add .`.
Before each PR, run required tests, Ruff and all three import contracts. Record
exact checks, commit hashes, limitations and PR links here after completion.
Next: finish full pytest, record its exact result, then push and open one PR into
main with before/after results and the sole core change. No unrelated web edits.
The context workflow and prior summaries are committed; refresh these notes at
the next verified PR/feature boundary without assuming pending checks passed.

See [AGENT_FUTURE_NOTES.md](AGENT_FUTURE_NOTES.md) only for the related task's
details. Update this summary after each feature instead of carrying the whole
conversation into subsequent work.

Final verification: full pytest **3,963 passed, five legacy toolkit skips**;
Ruff clean, all three import contracts kept, all 39 web tests pass.
All 480 corrected cells completed with zero errors. PR targets main.
