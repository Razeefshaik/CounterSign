# CounterSign agent state

Updated: Nov 3, 2026. Context keeper: **Professor Pickle**. This summary does
not replace AGENTS.md, CLAUDE.md or the required source documents. Read those
and only the relevant future notes before changes. Never store secrets,
environment values, real emails or raw model output here.

## Project

The model reads and proposes; the deterministic shield and executor authorize
and perform financial writes. PostgreSQL owns state, reservations and the inbox.
Money is integer minor units. Mock, staged, sandbox and real-model evidence stay distinct.

## Current work: C46 compact demo controls

- Active branch `razeef/c46-compact-demo-controls`, based on main `91eb0f0`.
  Feature commit `3c4a4f7`. No push or merge for this task.
- `web/demo.mjs` replaces the large floating dock with exactly Next step and
  Restart, a small genuine step/total, next-step/context tooltips and screen-reader
  status. Errors remain visible as a small popup. One POST per click, busy and
  complete states, restart confirmation and polling retain their existing logic.
  Bottom-right strip stays below 320x60; the simulation banner remains visible.
- Hero's flowing 3px rim returns to normal pink #ec4899. The rest of the interface
  keeps C45's matte rose accents. Tests updated in views/redesign/brand.
- Verified: **70 Node tests passed**, **23 Python demo-bar dashboard tests passed**
  (`tests/dashboard/test_demo_bar.py`), syntax and Ruff passed; all three import
  contracts kept. **22 browser checks passed** across five widths, both themes
  and overview/decision routes, with zero writes or runtime exceptions. The
  checker waits for live maker data; owner-operated step changes are allowed.
- Local Docker `countersign-c45` at loopback 8180 was started after the stale
  runtime socket folder was backed up. Full 11-stage hosted smoke passed before
  this UI update; new dock files were copied into that same running container,
  without posting any staged actions. No reset, environment-file reads, real LLM or
  real PayPal calls for C46. C45's full-backend limitation below still applies.

### C48 MCP gateway (Nov 3; ready for Claude review)

- Isolated worktree `CounterSign-c48`, branch `razeef/c48-mcp-gateway`, base
  `1303ad2`. Commits `610ccaf` (binding), `6f1a30f` (gateway), `abe5bc8`
  (docs/evidence) are pushed. Feature merge `60a41d9` includes upstream main
  `9eefa6e` and C52's test-only clock fix `c32d16c`; no merge to main.
  Final verification/context commit and push remain pending.
- Official MCP SDK gateway exposes exactly six reads and five proposal tools.
  Preview defaults to rollback; guarded demo/mock HTTP forces preview. Local
  stdio record mode uses the extracted internal-agent binding with external
  client attribution and audit source. No shield or executor changes.
- Verified by the primary agent: final focused run **23 passed** (19 gateway,
  four persisted-row characterization), earlier **43 unchanged agent/harness
  passed**, **28 approval tests passed** after the upstream fix, **70 Node
  passed**, Ruff clean and four import contracts kept. Runtime code is unchanged
  since the focused pass.
  SDK stdio preview/record and HTTP transcripts include BLOCK and plain
  NEEDS_OWNER; actual dashboard HTTP and owner approval are tested. A stale
  approved record is refused at execution with no mock refund.
- Decision screenshots in `design-demos/reference/c48/` cover light, dark and
  390px phone; checks found no overflow/runtime errors, retained the simulation
  banner and used reduced motion. Fixture is explicitly $540, versus the $60
  walkthrough. At the owner's request, broad pytest was intentionally stopped
  at 21%; no full-suite pass is claimed. Feature and important checks are done.
- Use only `countersign_c48` on PostgreSQL 55433 and preview port 8191. No
  environment-file reads or real PayPal/model calls. See the C48 future note.

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
C45 integration/push authorization was task-specific; C46 has no push or merge
authorization. Full tests require
the explicit database URL on 55433 and the toolkit test setting. On Windows use
SelectorEventLoop. Consult the matching section of
[AGENT_FUTURE_NOTES.md](AGENT_FUTURE_NOTES.md); older entries are history, not status.
Historical Oct 27 owner items were the FAILED replacement fix, Cloud Run deploy
and configured-model runs; verify current status before resuming them.
