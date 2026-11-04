# C66 operational AG Grid showcase

Verified Nov 4, 2026, in isolated `CounterSign-c66`, branch
`razeef/c66-ag-grid`, based on integration `upstream/main` **745c9b4**
(includes C62 merge134c610). The local `origin` fork is behind integration;
no original-checkout files or other worktrees were changed. Runtime commit
**6835338**. Evidence commit **1b664f4**. [PR #18](https://github.com/Razeefshaik/CounterSign/pull/18) targets main, open and non-draft. No merge.

## Delivered Community features

| Grid | Features now used |
| --- | --- |
| Incident desk | Stable `getRowId` plus `applyTransaction`, cell-change flash, Needs owner/Money held/Blocked/All filter-and-sort views with counts, floating text/date/number filters, multi-sort, pinned identity column, filtered pinned money total, Enter/click existing incident inspector, filtered CSV and column-state persistence/reset |
| Execution history | Same Community transactions/filter/sort/export/layout controls; Blocked by shield/Owner signed/PayPal writes/All models; pinned action; UTC date filter; integer amount sorting; known rule chips link to the existing decision explanation; proposal-bound records open the existing decision with Enter/click |
| Payout runs | Same controls; Waiting/Held back/Paid/All; observed reserved obligations have distinct stable IDs; pinned maker, integer amount filters/sort, filtered currency totals; Enter/click opens existing run reference details |
| Makers | Same controls for small and large lists; Has held share/Unverified payee/All; pinned maker, amount/held numeric filters and totals; Enter/click existing maker money-map dialog |

All grids use vendored **AG Grid Community32.3.9**. No Enterprise modules,
licence, dependency or vendor edits. Row grouping, master detail and integrated
charts were deliberately not used: ordinary saved-view buttons, pinned totals
and the existing detail surfaces provide Community alternatives.

## Data semantics and scope

Saved counts describe each view's loaded source records before its quick search
or native column filters; pinned totals describe all filtered rows across pages.
Currencies are summed separately with safe integers. Missing monetary values are
unavailable. Run All includes waiting, previously described resend and held
shares; its row total is not the payout manifest. The existing API run total is
retained above the grid. A replacement summary does not prove a new item's state.
Audit amounts summarise available event amounts, not net cash or unique payments.
PayPal writes selects recorded PAYPAL_RESPONSE events, including refusals; it
makes no successful-payment claim. Owner signed accepts OWNER_APPROVED and
OWNER_COUNTERSIGNED; it does not label plain approvals as passkeys.

Column layout is per grid and local browser viewer, with an optional supplied
viewer key; no login, identifier, email or API change was added. Storage denial
is caught. CSV uses native filteredAndSorted export, formats existing columns,
excludes summaries and prefixes spreadsheet formula/control-leading values.

Audit events with no bound proposal have no existing decision route and remain
without a fabricated link; Enter was verified on an actual bound event. Global
scrolled desktop screenshots expose the existing sticky-header overlap with the
simulation banner; the phone banner remains readable. Shared styles.css was
explicitly forbidden, so this existing global layout issue is left for its owner.
No universal accessibility or every-record-detail claim is made.

## Checks actually run

- **136/136 Node tests passed**, including nine new C66 tests for saved models and
  counts, integer/date comparators, filtered multi-currency totals/overflow,
  formatted/formula-safe CSV, layout validation/storage failures, stable row
  transactions/reduced motion, actual projection fields, every renderer escaping
  text, and mounted update/storage denial. Existing tests now include date filters
  and real reserved run rows. Final log: TEMP/countersign-c66-node-final.log.
- **One Docker build**, `countersign-c66:verified`, image inspect ID
  `sha256:b3add1c08ae2dc690f25374a6811b6642a177da0dbe808759598b2892c9afdd7`,
  inspect size **255259045 bytes**. Product source is6835338; later commits only
  capture evidence and handoff. No pytest, as explicitly required. No Python edits,
  so Ruff was not required/run.
- Own `countersign-c66-preview` on **127.0.0.1:8200** booted Friday step6 with
  checkpoint friday. Its private internal PostgreSQL never exposes host55433;
  no other database or preview port was touched.
- Actual headless Chrome drove Friday6 through11 using exactly five existing
  demo/step POSTs. The incident grid retained the exact API; genuine cell-change
  flash was observed after reduced motion was disabled. Final demo complete.
- Native models matched every saved-view count; numeric-filtered CSV downloaded
  via Community on all four grids (3/0/5/3 rows, excluding summaries). Audit's
  zero-row numeric export is honest: none of the49 initial events carried money.
  Browser exercised Enter, column-width storage, reset, refresh preserving native
  API/sort, light1440, dark incident, phone390 on all four grids, pinned important
  columns and storage-denied fresh render/reset. Zero runtime exceptions or
  console warnings. JSON records exact observations, not screenshots-only claims.
- Six screenshots and four downloaded CSVs are beside this README. All screenshots
  personally inspected. Browser helper: `node web/tests/c66-capture.mjs
  design-demos/reference/c66 --week` against an own disposable Friday container8200;
  it mutates only that container's scripted mock controls. Never run it on a shared
  or real-money service. No real PayPal/model calls or environment-file reads.

## Shared integration edits

Runtime edits outside grid.mjs/css and new helpers are limited to app.mjs:21/102/181
(preserve incident API/update callbacks and clear native search), command.mjs:102
(preserve maker API and support every list size), workspace.mjs:12/24/61/121/133
(preserve same-route run/audit grids and update surrounding run facts). Decision
append: docs/DECISIONS.md:653, `Nov 5 C66 AG Grid` (heading requested by owner).
Professor Pickle updates only the two context note files. No API/contracts,
root README, submission docs, styles.css or vendor changes.

## Suggested README text (not applied)

CounterSign uses AG Grid Community for the incident desk, history, payouts and makers.
Live rows update by stable ID and flash changed cells while preserving the working view.
Saved views, floating filters and filtered integer-money totals expose the operational records.
Keyboard detail access, safe filtered CSV and remembered column layouts work in both themes.
All totals describe loaded records; mock/staged evidence remains explicitly labelled.
