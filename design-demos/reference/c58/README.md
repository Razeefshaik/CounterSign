# C58 evaluation explorer: verification and handoff

Worktree: sibling `CounterSign-c58`, branch `razeef/c58-eval-explorer`, based on
fresh upstream/main `97a464b`.
Commits: `01656d3` bundle/tests, `dae163b` explorer/integration, `f7feb76`
browser/evidence, `0959a4d` context. [PR #17](https://github.com/Razeefshaik/CounterSign/pull/17)
is open against main, non-draft. Branch pushed to both remotes; no merge. The original checkout and other worktrees, ports
8180/8196 and databases are untouched. Preview uses only port **8197** and contract
fixtures; these source-only checks need no database.

The explorer reads the original, published C31 experiment, not later targeted
reruns: **480 model-disabled mock runs**. The bundler projects only allowlisted
metrics, checks the complete unique cells, sorts deterministically and writes LF.
It reuses C50's public guard and adds known API-key/token signatures, including
fail-closed checks on fields that would otherwise be dropped. No environment
file, real model, PayPal, new project dependency or contract change is involved.

| System | Runs passing | Scenarios at 5/5 |
| --- | ---: | ---: |
| CounterSign, model disabled | 170/240 | 34/48 |
| Rules-only | 215/240 | 43/48 |

All four named invariant totals are **0**. These are historical measured counters,
not real-model/sandbox results or proof of universal safety. The three presets
show **70** CounterSign failures, **30** paired comparison rows (three scenarios,
both systems), and **0** rows with any invariant above zero. Comparison scenarios
are the two stale-approval cases and `F_triage_buyer_declines_refund`.

## Checks actually run

- **18 Python tests passed**: bundler determinism, exact/nested allowlist, refusal
  of planted private values and malformed/incomplete/duplicate sources, complete
  published totals and unchanged output on refusal.
- **109 Node tests passed**, including seven new analysis/columns/preset/totals/
  empty-state/escaping/bundle validation checks and the existing web regressions.
- `ruff check .`: clean. Import-linter: **four kept, zero broken** (C48 added the
  fourth; older references to three are historical).
- No full pytest, per the C58 task. Claude runs integrated validation later.
- Actual headless Chrome on the isolated fixture preview: light 1440x1080,
  dark 1440x1080 and phone 390x844, all reduced motion. Each exercised all presets,
  filtered pinned totals, system external filters, quick search, pagination 25,
  metrics panel and selection into existing replay evidence. The simulation
  banner remains uncovered after scrolling. No page overflow, runtime exceptions,
  console warnings/errors or non-GET operational requests were observed.
- Each viewport downloaded a genuine Community CSV with **70 filtered rows**
  plus its header, excluding pinned totals. Files: `downloads/light/export.csv`,
  `downloads/dark/export.csv`, `downloads/phone/export.csv`.

`browser.json` records the three viewport runs. There are **24 PNGs**:
`{light,dark,phone}-{all,failed,comparison,invariants,details}.png`, plus
`{light,dark,phone}-{failed,comparison,invariants}-grid.png`.
The grid-only images show the actual zero-invariant empty state and filtered
pinned totals, not just the preset controls. Screenshots were visually reviewed.

The reproduction script is `node scripts/c58_capture.mjs`; it accepts only its
fixed local preview origin and creates its own headless browser/profile. Run it
from this worktree with the fixture server already on 8197. The bundler is
`python scripts/bundle_eval_runs.py`. No new library is required for either.

## Minimal existing-file insertions

All product integration insertions are fenced C58 blocks; styles.css is unchanged.

| File and line | Insertion |
| --- | --- |
| README.md:227 | Results explorer pointer |
| README.md:476 | Dashboard AG Grid explorer entry |
| web/README.md:3 | Build, interaction and provenance notes |
| web/index.html:13 | New scoped stylesheet |
| web/replay.mjs:3 | Explorer import |
| web/replay.mjs:106 | Existing trace-only selection callback and disclosure |
| web/replay.mjs:131 | Explorer host above original replay content |
| web/workspace.mjs:56 | Hide unrelated live-record note on historical replay |
| web/results/.gitattributes:3 | Preserve the generated artifact's LF bytes |
| docs/DECISIONS.md:606 | One appended requested Nov 4 C58 section |
| docs/AGENT_STATE.md and docs/AGENT_FUTURE_NOTES.md | Professor Pickle feature handoff |

New implementation, tests, generated data, scoped CSS and capture script are in
new files. No contract change or additional dependency was needed, and nothing
required stopping for owner clarification. The worktree was used as requested;
no edits were copied into the original checkout. No merge or deployment.
