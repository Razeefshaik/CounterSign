# CounterSign agent state

Updated: Oct 27, 2026. Context keeper: **Professor Pickle**. This summary does
not replace AGENTS.md, CLAUDE.md or the required source documents. Read those
and only the relevant future notes before changes. Never store secrets,
environment values, real emails or raw model output here.

## Project

The model reads and proposes; the deterministic shield and executor authorize
and perform financial writes. PostgreSQL owns state, reservations and the inbox.
Money is integer minor units. Keep mock and sandbox evidence separate.

## Current work: M21 evaluation gaps and hosted readiness (prompt 09)

- Branch `mate/M21-eval-gaps` from `upstream/main` `f330b3f`; code `41ff10f`,
  HTTP smoke/docs `d7d4ad9`, results `057a595`. No PR yet. C31 is merged
  (`bb77df0`); old merged local `mate/M18-eval-fix` was deleted.
- Both key-window scenarios omitted SCHEDULE_PAYOUT_RUN because a lost response
  leaves the proposal EXECUTING although the matching run approval was consumed.
  The adapter now records scheduling once per proposal when that run's snapshot
  approval has non-null used_at; later EXECUTED observations deduplicate. This
  records observed scheduling, not completed payment. Grading is unchanged.
- Targeted three-scenario k=5 rerun: both key-window cases pass 5/5 (each was
  0/5); ONHOLD -> FAILED remains 0/5. All 15 cells have zero errors, measured
  invariant counts, model calls and tokens. Evidence, original 15 before rows,
  redacted original-adapter grader inputs and verified manifest hashes are in
  `eval/results/2026-10-27-m21-three-gaps/`.
- ONHOLD -> FAILED is a real core gap: `_plan_replacements` in
  `approvals/service.py` selects only `_returned` latest attempts, leaving the
  failed allocation scheduled and planning at NO_PAYABLE_OBLIGATIONS. Core is
  untouched; its fix is reserved for Razeef/Claude.
- `scripts/hosted_smoke.py` uses public HTTP only: reset, 11 week steps, Friday
  108600 minor units once, final cash 35400, B's non-cash reserve 5400 and four
  playground attacks returning BLOCK/written=false. The final rebuilt Docker
  image `countersign-m21:verified` passed the full smoke locally again.
- One CPU / 512 MB: cold first meta 200 in 4.181 s including Docker start;
  post-week Docker stats 137.8 MiB; cgroup current 196636672 and peak 244563968
  bytes. Docker displays 860 MB total image usage; image-inspect Size reports
  204439669 bytes. All three fake-credential probes exit 64, variable names only.
- Completed checks: focused 52, hosted guard/smoke 19, adapter unit 26 and web
  47 tests pass; Ruff clean; three import-linter contracts pass. Selections may
  overlap. Full pytest: **4004 passed, 5 skipped in 986.70 s**, exit 0, with
  explicit test DB/toolkit settings. Temporary log: `countersign-m21-full.log`.
  Final JSON privacy scan, code identity and original scenario hashes pass.
- No web edits, core fixes, `.env` reads or real model/PayPal calls. No deployment.

## Continuation

Record the PR reference before handoff; final validation is complete. PR base is
main. The owner authorized independently ready future tasks in separate
main-based task branches and PRs. Inspection found M19 replay already exists
and no new prompt 10; no new task is selected. Current prompt forbids web edits;
core FAILED repair, actual deployment and configured-model runs remain with their
owners. Consult the M21 section of [AGENT_FUTURE_NOTES.md](AGENT_FUTURE_NOTES.md).
