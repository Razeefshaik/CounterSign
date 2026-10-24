# CounterSign dashboard (M06, M10)

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
