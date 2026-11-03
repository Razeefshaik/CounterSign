# CounterSign dashboard

<!-- C49 -->
## Nov 3 AG Grid working tables (C49)

AG Grid Community **32.3.9**, already vendored with its MIT license, enhances
the incident desk, execution history, payout run lines and maker lists above
four makers. No dependency or CDN was added. `grid.mjs` owns pure column/row
projections plus the lazy loader and mount; `grid.css` maps the existing light
and dark tokens to `--ag-*`. Theme changes need no reload. Reduced motion disables
row animation. Phones scroll columns inside the grid, not the page.

- Incidents: sort numeric minor-unit risk amounts, search all row fields with
  the existing quick search, filter columns, and click a row or press Enter to
  open the same incident detail. Makers and disputes are joined by API IDs.
- History: filter verdict and actor, search action/rule/references, and follow
  proposal/operation references to the associated decision. An operation link
  requires a proposal association from the API's audit rows; orphan references
  remain plain text. The original detail timeline remains the fallback.
- Runs: new lines and referenced resends share one grid; the API's exact run
  total is pinned below. A planned resend does not borrow the previous item's
  status. PayPal item status needs an item ID. Full idempotency references and
  held-back shares remain available below the grid.
- Makers: up to four cards stay unchanged. Above four, the existing state
  controls drive a paginated grid (10 rows per page); owner-needed cards remain
  above it. Any row can open its maker dialog, with a graph read on demand for
  makers beyond the map's first six. No extra makers are added to the money map.

Money stays integer minor units in row data and is formatted only by cells;
missing/invalid values display a dash. Renderers escape API text and mask emails.
Only owner-needed states use rose stamps. Each grid is destroyed when its host
is replaced. The vendor script/styles load on the first relevant view only;
while loading or on script failure the existing accessible HTML stays available,
with a standard-table notice on failure. Approval and payment logic are unchanged.

Node tests cover column contracts, mapping, escaping, numeric formatting, state
colours, pinned totals, activation, asynchronous mounting and missing-vendor
fallback. `tests/dashboard/test_grid_assets.py` checks the real static mount.
Browser evidence is in `design-demos/reference/c49/`; its scale sample is explicitly
browser-only contract fixture data, not a real marketplace or financial run.
<!-- /C49 -->

## Nov 3 compact demo controls (C46)

The staged-week dock is now two small bottom-right buttons, **Next step** and
**Restart**, with an exact step count. The next step and scripted/mock context
remain in accessible labels/tooltips; the persistent simulation banner, header
clock and overview timeline retain their roles. Busy/completed disabling,
single-request clicks, restart confirmation and polling are unchanged. Normal
status is announced to screen readers; errors appear in a small notice above
the controls. The hero's 3px flowing rim uses its earlier pink colour; other
owner-attention accents remain muted matte rose.

Checks: 70 frontend tests, 23 Python demo-control tests, and 22 read-only browser
checks across five widths, both themes and overview/decision routes. Browser
checks used the real local backend without posting any demo actions.

## Nov 2 selected hero and integration (C45)

The approved Combined study now opens the real overview: Porcelain's frame and
pane proportions, the blue background, local satin-blue / pink spheres and a
fuller pearl-white / pale-blue oval at the lower right. The approved pink-ball
position is retained. A 3px matte-rose reflection follows the existing glass rim;
Pause animation and reduced motion stop the decorative movement. The new
corner-and-check SVG is the favicon, sidebar mark and hero title highlight.

The hero contains identity copy only. The actual finding and approval link remain
bound to the dashboard data immediately below it; no amount or live state is
copied from the study. Existing empty/loading/error states and approval logic
are retained. `hero.css` scopes the new art; the four transparent WebP assets total
548,910 bytes. Their exact material prompts are in `assets/hero-prompts.json`.

Owner-attention accents now use muted matte rose in both themes, including the
map, money bars, badges and panels; dark rose text retains contrast. Local cream
styling is preserved, and C43's spacing retains the original 8px card rims.

## Nov 2 matte card spacing (C43, branch razeef/c43-matte-card-fix)

Light cards use a subtle warm cream (`--surface: #fbf8f1`) with less saturated
backdrop blur. The original glass rims and dark palette are preserved. Decision,
split-view, replay and overview figure cards have 28px gaps, leaving 12px visible
between their existing 8px rims. Grid tracks may shrink without pushing adjacent
cards out, and the sticky decision column clears the desktop header.

Validation: 63 frontend tests; 81 fixture-based browser layout checks across eight
routes at 390, 768, 1080, 1440 and 1606px in both themes, including rim separation,
page overflow and sticky-header clearance. No financial behavior changed.
## Nov 2 overview polish (C44)

The display-only week milestones use the owner-selected generated satin-blue
material, served locally as `assets/milestone-blue.webp` (128 × 128, 7,352 bytes).
The material was generated with the built-in image tool using the owner's satin
glass-metal reference; the prompt requested a seamless blue sphere, fine grain,
polished reflections and no coloured bands. Existing day states and labels stay
derived from the demo steps. The current marker is static rather than pulsing.

Friday's comparison uses two aligned bars on one scale, labelled “Simulated”
and “Recorded plan”. The disputed portion stays visible in the simulated payout;
CounterSign's bar stops at its planned payout, without a hatched tail that could
look like money being sent. Amounts remain sourced from the saved comparison,
not current balances. Missing or invalid amounts show unavailable.

Validation: 67 Node tests passed, syntax check passed, and 20 read-only browser
checks cover five widths, both themes, all eight routes and reduced motion.
Browser evidence uses public contract fixtures and the captured mock Friday,
not a running backend. No Python suite or real services were used for this UI work.

## Nov 2 glass command center (C42, branch razeef/c42-glass-command, built on C39)

- **Backdrop and glass.** The owner's frosted backdrops sit behind the page: `assets/backdrop-blue.webp` in light mode, `assets/backdrop-night.webp` in dark mode (rendered images, 2400 × 1500 WebP, about 130 KB each). Every surface that carries text is glass at 72% or more (`--card`, `--glass`, `--sheet`), so copy keeps 4.5:1 over the darkest part of either image; text tokens on glass were darkened to match (`--ink-3` `#46526b`, `--accent-ink` `#1f47ad`, `--highlight-ink` `#9e1a5c`, `--bad` `#a82a33`). One token set per theme; components never repaint per theme.
- **Sidebar.** A labelled glass sidebar grouped by job: Command center; Act (Decisions, Incident desk); Money (Payout runs, Cash, Money graph); Records (Execution history, Evaluation replay); Lab (Try to trick the agent, shown only when the playground exists). Owner counts are pink; other counts are plain. Phones keep the scrolling nav row.
- **Command center, top to bottom.** The headline and finding with the one action ("Needs your signature" strip linking to the exact decision); four figures, each with the line that makes it actionable (`kpiCards()`); the makers; where the money stands (one bar of what is owed now, plus whether the balance covers the run); the week and Friday's comparison; the shield scan and activity; then the money map with its explanation panel.
- **Makers at any count** (`makerList()`). Up to four makers read as cards. Past four, the makers waiting for the owner stay as cards and everyone goes into a table with state filters and ten rows at a time. Cards and rows for makers on the map open them there; the map still draws six, and totals now count every maker.
- The C39 deck, sticker, coins and icon rail are retired.
- **Review fixes.** The headline counts every maker and matches the owed figure below it; map labels are readable at card size and sit below maker and PayPal nodes; the explanation panel skips Friday's bars when the overview shows them (`storyPanel({bars})`); the overview leaves room for the demo dock.
- **Maker dialog.** A maker card or table row opens that maker's money map in a dialog (`#maker-dialog`): select a category, then a record, to see how it connects. Esc, the close button or the backdrop closes it. The money map at the bottom keeps its own selection.

## Oct 31 journey overview (C39, branch razeef/c39-glass-journey)

Same theme and pieces as C38, arranged around the decision: a headline written from the model (`headline()`), the agent's latest request as a message, the available balance with "Review and sign" (links to the waiting decision), the week as a path of stops (display only), the makers as a stacked deck (click to reorder; "See details" opens the front maker in the map) and a pink sticker for money held back for disputes. Below: KPIs, Friday's comparison, the money map with its panel, the shield scan, activity and status bars.

## Oct 31 glass overview (C38, branch razeef/c38-glass-atrium)

- **Theme.** Light glass by default: neutral ground, soft cornflower light behind blurred chrome, near-opaque cards. Folio blue `#2a56c9` is the primary; cornflower `#6b9fe4` supports it; pink `#ec4899` only marks what needs the owner. Dark remains a toggle.
- **Overview layout.** Title "This week" with one finding sentence, week progress, a KPI strip, then a grid: money map + explanation panel, shield scan + maker folios, status bars + activity.
- **Money map** (`plot.mjs`, pure functions): a network plot. Node area follows the amount, line width follows money moved, arrows show direction; pink dashed is waiting for the owner, navy dashed is held. Dots move only on paid or in-flight edges. The shield shows one bar per rule check run this week.
- **Shield scan.** Each proposal is a blip: cleared inside, waiting for the owner in the holding loop, stopped in the turned-away sector. Blips link to their decision.
- **Folios, bars, activity.** Maker cards (selected maker in folio blue) open the drill-down; stacked bars per maker plus the owner's signature; the latest proposals with the shield's answer.
- The C37 drill-down, panels and model (`scene.mjs`) are kept and restyled flat.

## Oct 28 command center (C37)

The dashboard now opens on **Command center** (`#overview`), a picture of the week's money:

- **Money constellation** (`scene.mjs`, pure functions). PayPal's balance flows through the CounterSign shield to each maker. The AI agent and the owner feed into the shield, and a held-back vault collects disputed shares. The shield's ring has one tick per rule (16), coloured by the decision that matters now.
- **Particles mean something.** Cyan: money in flight; teal: paid; amber: held back for a dispute; violet: agent proposals; red: blocked proposals bursting at the shield. A run waiting for the owner is dashed and labelled "scheduled", with no moving particles, because no money has moved.
- **Drill-down.** Selecting a maker zooms into its records from `GET /money-graph?maker_id=`: category cards (orders, shares, PayPal payouts, disputes, holds) with drawn connectors, then a fan of records. Selecting a record explains its links in words. Esc steps back.
- **Side panel.** It tells the week by default: the latest step, the owner's waiting signature, Friday's saved comparison (naive $1,500 against CounterSign $1,086 with $414 held) and the three safety moments, each shown only once recorded. Selecting the shield, agent, owner, PayPal balance or vault explains that node.
- **Week rail and dock.** A vertical day timeline sits in the scene. The demo bar floats as a dock at the bottom of every page.
- **Theme.** Dark by default, with a light toggle in the top bar (stored per browser). The scene stays dark in both.
- **Motion.** Ambient motion replaces the C32 "under 300 ms" rule on this page only. `prefers-reduced-motion` removes particles, rotation, draw-in and count-up.
- **Honesty.** Every number comes from the read-only API. A balance that has not been read shows "—", never $0. A graph not rooted at a maker is never attributed to it. Names and emails are escaped and masked.

The artwork (spheres, halos, particles, icons) is original SVG and CSS. No third-party images, fonts beyond the existing Google Fonts families, or libraries were added. Screenshots are in `docs/media/command-center*.png`.

Tests: `node --test web/tests/*.test.mjs` (54, including `scene.test.mjs`).

## Oct 27 redesign (C32)

The dashboard was rebuilt for a first-time reader who has about a minute:

- **Every page leads with one plain-English sentence** built only from API records (for example "Pay Maker B $486.00 and Maker C $300.00, and resend Maker C's returned $300.00 payout. Total $1,086.00."). Contract codes, hashes and IDs stay on the page, in titles and in "Technical details".
- **Incident desk:** a quiet four-figure ledger (open incidents, decisions waiting, held back from disputes, unreserved cash), one callout when something waits for the owner (payout runs included), and a keyboard-reachable incident table. <!-- C49 --> The vendored Community grid now enhances this table; see the C49 section above. <!-- /C49 -->
- **Demo bar:** the staged week as a day-grouped stepper.
- **Decision:** the summary sentence, the shield's checks with plain names (`words.mjs`), the owner's signature and approval, and "How this was decided" (read, proposed, shield, owner, sent).
- **Try to trick the agent** (`trick.mjs`, demo mode on the mock only): runs the proposal a fooled agent would make through the real shield via `POST /api/v1/playground/attempts`. The server rolls every attempt back.
- **Design rules:** one accent colour; tinted neutrals; a ring shadow instead of borders; IBM Plex Sans and Mono; tabular numbers; motion under 300 ms on named properties only; hover effects only for precise pointers; reduced motion respected; light and dark themes.

## Earlier notes (M06, M10, M19, M20)

M19 adds **Mock replay** (`#replay`): F01 and the settled-obligation scenario,
paired by run ID, scenario, repetition and seed. The bundled four runs were
executed with M11's `run_one`, `rules_only` and CounterSign, `model=none`.
They are mock checks, not real-model benchmark results. The settled CounterSign
run reports `EVALUATION_FAILED` despite zero invariant violations and completed
payouts; that runner outcome is preserved visibly, not rewritten as a pass.
Neither rules-only nor CounterSign duplicates a payout in these observations.

Use **Next action** or **Show all actions** to inspect recorded traces alongside
final mock ground-truth payout items. Final payouts are a separately labelled
observation, not a reconstructed intermediate balance. Select **Baseline LLM**
and load M18's `runs.jsonl` later; absent runs remain unavailable. Summary-only
files show metrics, with traces and payouts explicitly unavailable.

`web/replays/evidence.json` is a separate optional, public replay artifact keyed
by the same sample fields plus system and `ground: mock`. It holds sanitized
`actions` (at/action/outcome/approved_by_owner) and final `payout_items`
(id/amount/status/money_left_account/created_at); no receiver, request payload,
credentials or untrusted notes are saved. It does not change CONTRACTS 6.3.
The browser accepts both files locally without uploading them. Sandbox files,
invalid data, duplicate samples and files over 10 MB are refused.

Reproduce the bundled evidence using a migrated disposable `_eval` database:

    python web/tests/build_replay.py --database-url postgresql://.../web_replay_eval

No model, `.env`, sandbox credentials or real PayPal calls are used. The
generator wraps teardown to capture the public mock control API and SystemTrace
without changing `eval/` or `mock_paypal/`.

## M20 validation and media

The app uses its existing layout with the Achromatic ledger direction (seed
1286248125), Segoe UI, neutral light/dark surfaces and green on status and money
relationships. Labelled navigation is retained for the seven destinations.
The small SVG mark combines ledger lines with the second signature. No fonts,
application dependencies, model calls or sandbox calls were added.

Reasons for the changes, kept here because this task permits only `docs/media/`
under the shared documentation directory:

- Demo metadata reads the current mock clock and fails closed with 503 if it is unavailable, so a wall clock cannot keep an expired staged proposal approvable.
- Decision refresh reloads metadata before deciding eligibility, so staged expiry is not pinned to the first page load.
- Replay evidence is separate from unchanged M11 metrics, so summaries never masquerade as action traces or ground-truth payouts.
- API failures do not switch a hosted workspace to fixture data, so an unavailable source cannot look like a working marketplace.
- Neutral surfaces and explicit dark tokens keep amounts, staged labels and status text readable in both themes.
- Empty grids shrink and explain the next step, so a cold start and a restarted week remain usable.

Validation on Oct 27, 2026: 138 dashboard tests passed against PostgreSQL 16
with both test environment variables set; 39 Node tests passed; `ruff check .`
passed and all 3 import-linter contracts were kept. An exploratory full-repo
pytest run was stopped; these results cover the dashboard task scope.
Lighthouse 13.5.0 measured accessibility at 100/100 on the incident desk.
The rebuilt final Docker image passed 28 additional checks across all seven
views, desktop/phone widths and light/dark themes, with no console errors or
JavaScript exceptions. The restart screenshot was verified against both the
API's empty incident list and the refreshed UI after clicking Restart week.
The compact validation record and PNG hashes are in `web/tests/verification.json`;
raw inspector and test output remain local under ignored `web/tests/artifacts/`.
Chrome completed the 11 staged UI steps, observed $354.00 unreserved, and
confirmed that restart clears the marketplace. There were no browser exceptions
in that run. Light/dark checks cover 1440, 768 and 390px on clean views and
1440/390px on populated views, with no document-level horizontal overflow.
The design inspector improved from 4 failures to 0; its remaining 41px blank-band
warning is the empty incident grid's centered message and preserved column headers.

The PNG-only set in `docs/media/` contains `desk`, `decision`, `runs`, `cash`,
`graph` and `replay`, each at desktop and phone width in both themes (24 files),
plus `clean-start.png` and `restart-week-dark.png`. All capture the local mock;
the persistent simulation banner is visible. Published screenshot state is
Friday's pending run, before the staged owner approval. No screenshot uses
sandbox credentials or real emails.

| View | Desktop | Phone | Dark desktop | Dark phone |
| --- | --- | --- | --- | --- |
| Incident desk | [PNG](../docs/media/desk.png) | [PNG](../docs/media/desk-phone.png) | [PNG](../docs/media/desk-dark.png) | [PNG](../docs/media/desk-phone-dark.png) |
| Decision | [PNG](../docs/media/decision.png) | [PNG](../docs/media/decision-phone.png) | [PNG](../docs/media/decision-dark.png) | [PNG](../docs/media/decision-phone-dark.png) |
| Payout runs | [PNG](../docs/media/runs.png) | [PNG](../docs/media/runs-phone.png) | [PNG](../docs/media/runs-dark.png) | [PNG](../docs/media/runs-phone-dark.png) |
| Cash | [PNG](../docs/media/cash.png) | [PNG](../docs/media/cash-phone.png) | [PNG](../docs/media/cash-dark.png) | [PNG](../docs/media/cash-phone-dark.png) |
| Money graph | [PNG](../docs/media/graph.png) | [PNG](../docs/media/graph-phone.png) | [PNG](../docs/media/graph-dark.png) | [PNG](../docs/media/graph-phone-dark.png) |
| Mock replay | [PNG](../docs/media/replay.png) | [PNG](../docs/media/replay-phone.png) | [PNG](../docs/media/replay-dark.png) | [PNG](../docs/media/replay-phone-dark.png) |

Run from the repository root with the approved FastAPI/Uvicorn dependencies:

    python web/serve.py

Open http://127.0.0.1:8100. Until the dashboard API exists, the page explicitly
uses contract fixture data. After M07 it automatically reads the local GET API.
No PayPal credentials are used. The simulation banner comes from meta and
cannot be dismissed. Staged labels come from linked staged disputes.

AG Grid Community 32.3.9 is vendored under vendor/ with its MIT license.
No frontend install, build, remote font or CDN connection is needed at runtime.
Filters combine state, kind and search; selecting a row displays its decision.
At phone width the sidebar collapses and the table scrolls within its container.

Tests:

    node --test web/tests/*.test.mjs

The fixture examples derive from CONTRACTS section 2. Abbreviated example
IDs are expanded to full UUIDv7 strings. Additional incident/proposal rows
represent the same demo week and preserve the contract's data shapes.
M10 adds the decision, payout run, execution history, cash and SVG money graph
views. Open them from the sidebar; at phone width the navigation scrolls
horizontally. Incident rows include a Review decision link. Every view keeps the
simulation banner visible and renders API strings as escaped, masked text.

## M10 walkthrough

1. Start `python web/serve.py` and open http://127.0.0.1:8100.
2. In **Incident desk**, select Friday's payout run and use **Review decision**.
   The proposal includes the exact amount, parameters, evidence references,
   every supplied shield rule (including PASS), the payload hash and expiry.
   Use the record selector to inspect the blocked refund and its injection flag.
3. In **Payout runs**, verify the $1,086 total: Maker B $486, Maker C $300,
   and Maker C's $300 replacement. Expand your review through the obligations,
   attempt state, masked payee, PayPal references and idempotency keys. The
   replacement's previous attempt is RETURNED; $414 remains reserved, not paid.
4. In **Execution history**, inspect the audit actor, time, verdict, proposal and
   operation references, and details. Empty histories show an explicit empty state.
5. In **Cash**, verify $1,340 effective available minus $1,086 active cash
   reservations equals $254 unreserved. The $414 dispute earmark is separate;
   withheld money and earmarks are not deducted again. Review snapshot freshness.
6. In **Money graph**, inspect the dispute/order/obligation/reservation/maker
   links. Select another dispute to change scope. A static-only fixture has a
   graph for Maker A; other scopes explicitly show no linked records.
7. Repeat at phone width: all six navigation links remain reachable, wide tables
   and the graph scroll within their containers, and the simulation banner stays
   visible. Use keyboard navigation to reach the record selector and controls.

## Approval integration

The preview server mounts the read-only fixture API. It does **not** implement
core's approval endpoints or simulate successful approvals. Clicking Approve
in this preview reports that the core approval service is unavailable; Refresh
reloads the proposal. No money moves and no PayPal API is called by the browser.

When core mounts its owner endpoints on the same origin, the controls send:

- `POST /api/v1/proposals/{id}/approve` with the displayed `payload_hash` and
  `approved_by: "owner"`.
- `POST /api/v1/proposals/{id}/reject` with the entered rejection reason.

Only NEEDS_OWNER proposals with an unexpired hash can be decided. In live mode
the owner enters `X-Owner-Token` for that request; it is cleared from the input
and never stored in browser storage. Concurrent clicks send one request. A 409
invalidates the displayed proposal and reloads it, requiring another review;
the UI never automatically retries an approval. An unconfirmed response disables
the old controls until Refresh. A successful request also reloads the proposal.

Automated frontend tests cover these request and state-handling paths using fake
responses, including stale/expired approvals, unavailable endpoints, navigation
races, cash arithmetic, SVG relationships, escaping and email masking. They do
not replace visual browser QA or an end-to-end check with core's owner endpoints.
No new dependencies were added. Shared contracts and task status files are unchanged.

<!-- C50 -->
## C50: Sandbox proof

The Records navigation and quiet hero link open `#proof`. `proof.mjs` renders the
deterministic `results/sandbox-proof.json` bundle generated by
`python scripts/build_sandbox_proof.py`. The existing workspace router hosts it;
there are no new API calls or endpoints. `proof.css` uses the current glass tokens
and scopes every new style, including light/dark, 390px and reduced motion.

Findings, counts, observed IDs/statuses and Gemini decisions link to committed
GitHub source files. Absent fields stay unavailable. The 47 signature results
include one simulator check; six successful payout items exclude D7 fixtures.
Gemini used the mock, and its two WAIT choices are not invented shield BLOCKs.
The existing simulation banner remains visible throughout.

Run `node --test web/tests/proof.test.mjs` for rendering, provenance URLs, missing
fields, escaping, loading/retry and route checks. Builder privacy/determinism
tests are in `tests/unit/test_sandbox_proof.py`. See
`docs/walkthrough/c50-sandbox-proof.md` for scope and regeneration.
<!-- /C50 -->
