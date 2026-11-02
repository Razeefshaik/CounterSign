# CounterSign agent state

Updated: Nov 3, 2026. Context keeper: **Professor Pickle**. This summary does
not replace AGENTS.md, CLAUDE.md or the required source documents. Read those
and only the relevant future notes before changes. Never store secrets,
environment values, real emails or raw model output here.

## Project

The model reads and proposes; the deterministic shield and executor authorize
and perform financial writes. PostgreSQL owns state, reservations and the inbox.
Money is integer minor units. Mock, staged, sandbox and real-model evidence stay distinct.

## Completed: C45 overview integration

- Owner explicitly authorized integrating the selected design to main and
  pushing. Main was fast-forwarded to feature `6a8c096` and pushed to
  `origin/main`; the remote hash was verified on Nov 3. Integration worktree:
  `CounterSign-integrate`, branch `razeef/c45-overview-integration`.
- Original dirty main stylesheet was preserved in main commit `0c3ca93`; main
  is clean. This branch rebased on it, then replayed C43 as `c0d9134` and C44 as
  `5ec1eb5`. C43 spacing/matte cards and the original 8px rims are retained;
  the local thin-rim override was removed at the owner's request.
- Feature commit `6a8c096`: selected identity hero in `web/hero.css` and
  `index.html`, four optimized local WebP assets (548910 bytes total), selected
  corner/check mark at title/sidebar/favicon, and guarded static brand title
  plus animation pause binding in `command.mjs`. Genuine data-bound action and
  finding remain below the hero. Existing C44 milestones/comparison remain.
- Hero uses Porcelain pane heights 469/452/407, copied pink placement 6%/7%,
  thick pearl ring v4 and 3px pink rim. Global highlights use muted rose:
  light #c18da5/ink #80415f, dark #b88da4/ink #e4bfd2.
- Verified: **70 web tests passed**, **48 focused Python dashboard tests passed**
  (`test_schema_compatibility.py`, `test_results_bundle.py`), syntax checks passed,
  Ruff clean, all three import contracts kept. Eight production static-handler
  checks confirm the page, CSS, icon and WebP assets are served.
  Integrated browser checks **99 passed**: six widths (360/390/768/1080/1440/1606),
  two themes and eight routes, plus pause/reduced motion/zero writes; zero runtime
  exceptions. Evidence: `design-demos/reference/integrated`.
- Full backend pytest was not completed: the first process ended without a
  summary; a persistent retry was intentionally stopped at 23% after a long
  database integration section, with no failures reported. It used PostgreSQL
  55433 but omitted the optional test-toolkit variable (two skips visible).
  No full-suite pass is claimed. External log:
  `design-demos/.impeccable/integrate/pytest-final.log`.
- Read-only review's remaining `.waiting` hot-pink edge was fixed with a muted
  token-based color mix and a regression assertion. No app change was required
  for pause behavior. No environment-file reads or real API calls.

## Merged on Oct 27 (historical)

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
C45 owner authorization includes integration to main and push after verified
checks; do not claim either complete before checking Git. Full tests require
the explicit database URL on 55433 and the toolkit test setting. On Windows use
SelectorEventLoop. Consult the matching section of
[AGENT_FUTURE_NOTES.md](AGENT_FUTURE_NOTES.md); older entries are history, not status.
Historical Oct 27 owner items were the FAILED replacement fix, Cloud Run deploy
and configured-model runs; verify current status before resuming them.
