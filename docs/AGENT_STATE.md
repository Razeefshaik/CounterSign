# CounterSign agent state

Updated: Oct 27, 2026. This concise summary does not replace AGENTS.md,
CLAUDE.md, HANDOFF.md, DECISIONS.md, PLAN.md, CONTRACTS.md or TASKS.md. Read the
required source documents and relevant future notes before changes. Context
keeper: **Professor Pickle**. Never store secrets, environment values, real emails
or raw model output here.

## Project

The model reads and proposes; the deterministic shield and executor authorize
financial writes. PostgreSQL holds state/reservations/inbox. Money is integer
minor units. Mock, staged, sandbox and real-model evidence stay distinct.

## Current work: W2 story data

- Branch: `razeef/w2-story-data`, worktree `CounterSign-w2`, base `f330b3f`
  (C32 shield playground/dashboard redesign merged). Commits: `653c2c2` traces,
  `b278372` comparison/simulator/demo/contracts, `945b1c8` results bundle.
  Final full pytest passed; implementation pushed to `origin/razeef/w2-story-data`. No merge.
- CONTRACTS/meta advance to 1.0.6. Owner-authorized scope covers dashboard and
  dashboard tests, commander traces, proposal binding, scripted demo rationales,
  results bundler, and the additional simulator per-maker/audit extension.
  The only web artifact is generated `web/results/summary.json`; no UI edits.
- Commander records sanitized actual tool attempts, capped at 16, with fixed
  outcomes and no tool output/raw model text. Harness binds the trace into the
  proposal; dashboard exposes it or null for planners/older rows. Scripted demo
  rationales name their facts and say they are scripted.
- Demo/mock-only `GET /api/v1/demo/comparison` reads saved Friday simulation and
  bound run/reservation/history evidence. GET performs no simulation or PayPal
  call and reports unavailable when saved evidence is absent. Saved Friday is
  retained after the following week's run. Moments remain null until recorded.
- Simulator adds per-maker amounts/disputed shares without changing refusals.
  The mock CLI saves masked Friday evidence in the existing audit vocabulary and
  records linked duplicate-400 recovery proof. Intended Friday amounts are
  150000 naive, 108600 protected, 41400 disputed minor USD; Docker endpoint checks
  now verify these staged/mock figures.
- Results bundler derives model-disabled scoreboard/history and sandbox findings
  from checked-in sources. Real-model data stays null without configured results.
  Existing C31/reserve-observation source is 170/240 CounterSign (34/48 pass^5)
  versus 215/240 rules_only (43/48); both have zero error rows and measured
  invariant counters. These are model-disabled mock results, not AI proof.
- Ruff clean, all three import contracts kept (zero broken), requested three web
  test files **47 passed**. Full pytest: **4000 passed, 5 skipped in 1434.11 seconds**,
  exit 0; XML confirms 4005 tests, zero errors/failures. Both explicit test DB
  (55433) and isolated toolkit settings were enabled. Five skips require toolkit
  installed in the main environment; isolated toolkit checks ran. Temporary
  evidence: `countersign-w2-full.log`/XML. WAL settings restored to max 1024/min
  80 MB and checkpoint completed.
  The corrected stale contract assertion passes its targeted retest (1 passed).
- Docker image `countersign-demo:w2` rebuilt. Isolated check on port 18080 ran
  through step 11. Curl step 0 reports unavailable (`friday_plan`). Step 6 shows
  naive 150000/protected 108600/disputed 41400, A36000+B5400 reserved, all moments null.
  Step 11 retains the Friday root/saved naive timestamp, only B5400 remains
  reserved, and records one lost-response payment with duplicate400 linked true,
  injection blocks PAYEE_INTEGRITY+INJECTION_GUARD, and retry blocks SETTLEMENT,
  PAYOUT_STATE_MACHINE+CANCEL_BEFORE_REPLACE. All amounts are minor USD.
  The verification container was removed after completion; the owner's existing
  `countersign-demo` container on 8080 was untouched.
- Docker proposal evidence verifies scripted PROVIDE_EVIDENCE trace: model
  scripted, rounds 2, get_dispute/get_shipment_tracking, labelled Scripted rationale;
  meta contract version is 1.0.6.
- Changed core files: `agent/commander.py`, `harness.py` (binding only),
  `api/dashboard.py`, `simulator/service.py`, all under `src/countersign/`.
  Authorized scripts: `scripts/demo_week.py`, `scripts/bundle_results.py`.
- No real model or PayPal calls and no `.env` reads. Implementation push succeeded
  to `Razeefshaik/CounterSign`; these context notes accompany the handoff.

## Continuation

Use explicit file staging. Commits: `W2: lowercase summary`, one line, no
attribution. Push after verification; do not merge. Any PR targets main.
Current main/toolkit interpreters are in the original CounterSign worktree's
`.venv/Scripts/python.exe` and `.venv/toolkit/Scripts/python.exe`; verify paths.
Full tests require explicit database URL on 55433 and toolkit test setting.
On Windows use SelectorEventLoop. Consult the W2 section of
[AGENT_FUTURE_NOTES.md](AGENT_FUTURE_NOTES.md) for reusable details; older C31/M18
entries describe historical work and are not current task status.
