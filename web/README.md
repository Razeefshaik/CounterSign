# CounterSign incident desk (M06)

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
This shell contains no approval or money action controls (M10).
