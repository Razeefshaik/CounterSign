# CounterSign agent state

Updated: Oct 27, 2026. Context keeper: **Professor Pickle**. This summary does
not replace AGENTS.md, CLAUDE.md or the required source documents. Read those
and only the relevant future notes before changes. Never store secrets,
environment values, real emails or raw model output here.

## Project

The model reads and proposes; the deterministic shield and executor authorize
and perform financial writes. PostgreSQL owns state, reservations and the inbox.
Money is integer minor units. Mock, staged, sandbox and real-model evidence stay distinct.

## Merged on Oct 27

- **W2 story data** (`razeef/w2-story-data`, merged into main): contract 1.0.6;
  demo/mock-only `GET /api/v1/demo/comparison` reads the CLI-saved Friday
  simulation and recorded run history; sanitized `agent_trace` (16-call cap) on
  proposals, null for planners/older rows; scripted rationales say "Scripted:";
  generated `web/results/summary.json` via `scripts/bundle_results.py`.
  Full pytest 4000 passed, 5 skipped.
- **M21 eval gaps and hosted readiness** (PR #15, merged into main): adapter
  records SCHEDULE_PAYOUT_RUN once when the run approval is consumed and the
  proposal stays EXECUTING; both key-window scenarios 0/5 -> 5/5. ONHOLD -> FAILED
  stays 0/5 and is a core planner gap (`_plan_replacements` and `_replacement_ids`
  accept RETURNED only); its fix belongs to Razeef/Claude. `scripts/hosted_smoke.py`
  checks a running image over public HTTP; local 1 CPU/512 MB boot 4.181 s,
  137.8 MiB after the week, image under 1 GB. Full pytest 4004 passed, 5 skipped.
  Evidence: `eval/results/2026-10-27-m21-three-gaps/`, `docs/walkthrough/c27-hosting.md`.

## Continuation

Use explicit file staging. One-line commits with the task prefix, no attribution.
Push after verification; do not merge. Any PR targets main. Full tests require
the explicit database URL on 55433 and the toolkit test setting. On Windows use
SelectorEventLoop. Consult the matching section of
[AGENT_FUTURE_NOTES.md](AGENT_FUTURE_NOTES.md); older entries are history, not status.
Open owner items: core FAILED replacement fix, Cloud Run deploy, configured-model runs.
