# C49 AG Grid browser evidence

Runtime source: `fd09a09`, branch `razeef/c49-ag-grid`, based on main `454d2cc`.
Later commits correct a stale import-count test and add evidence/context notes;
they do not change the captured frontend. No merge or push to main is part of
this task.

## Scope and data

These are local **contract fixture previews**, not sandbox/real-model evidence
or a recorded financial run. The browser opened the C49 checkout on loopback
port **8192**. Existing public `web/fixtures/` supplied all base API documents.
For the many-maker path, browser interception added 21 clearly named
`Fixture maker NN` rows with deterministic integer amounts and varied states.
Two generated audit entries exercised needs-owner/stopped and actor filtering.
They were never written to the database. The preview stayed labelled
**Simulated: mock PayPal** and **Contract fixture preview**.

The actual vendored `ag-grid-community.min.js`, CSS and MIT license came from
the checkout. No CDN or Enterprise modules were used. The separate Python asset
test requests them through the real hosted FastAPI static mount.

## Captures

For each of `incidents`, `audit`, `runs` and `makers`:

- `<view>-light-1440.png`: desktop light;
- `<view>-dark-1440.png`: desktop dark;
- `<view>-light-390.png`: phone light;
- `<view>-dark-390.png`: phone dark.

Columns scroll inside the card at phone width. Screenshots scroll the grid into
view; the page header may already have scrolled behind the sticky top bar.
There are **18 PNGs**, including `native-filter-{light,dark}-1440.png` for the
actual AG Grid column-filter popup. The many-maker screenshots deliberately demonstrate
pagination and owner-needed cards; these illustrative figures are not production
or demo-week totals.

## Checks

`verification.json` records **31 passed browser checks**, zero JavaScript console
errors, zero page exceptions, zero warnings and **zero POST requests**:

- the three-maker overview does not request the vendor bundle;
- incident risk sorts by integer minor units, Enter opens the selected detail,
  and quick search preserves the sort while updating the visible selection;
- history's verdict and actor controls drive AG Grid filters;
- native column-filter typing works, and popup surfaces/focus inherit the app's
  live theme tokens in both light and dark;
- the complete payout total is pinned, including referenced resends;
- a 24-maker list pages ten at a time, filters to the owner-needed subset and
  opens an extra maker's dialog beyond the map's first six;
- all four grids fit the page in both themes at 390/1440 px, with the simulation
  banner visible and reduced motion enabled;
- blocking the vendor script retains the existing accessible HTML table and
  announces the fallback.

The static preview intentionally lacks backend API/demo routes, so their 404s
trigger the application's existing fixture mode. A blocked vendor request is
intentional in the fallback check; neither is counted as a JavaScript exception.

Reproduce using a local static server on 8192 that serves `.mjs` as
`text/javascript` and `.webp` as `image/webp`, then inspect the four routes.
The capture helper used the installed Chromium CDP client with an isolated
browser profile, not the owner's browser session. The temporary preview is
stopped at handoff; the test database `countersign_c49` is isolated on 55433.

## Integration notes for Aman

`grid.mjs` is the single Community bridge; `grid.css` owns all new styles and
inherits the theme tokens live. `styles.css`, `plot.mjs`, `index.html` and the
vendored files are unchanged. Keep `makerList()` as the HTML fallback contract.
The small C49 insertions in `app.mjs`, `command.mjs`, `views.mjs` and
`workspace.mjs` own the mounts; no approval handler, endpoint or financial write
was added. Operation references link only when an actual proposal association
is known; unidentified PayPal items and planned resend states remain unavailable.
