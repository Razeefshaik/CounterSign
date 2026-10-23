# Mock PayPal — M01

Local, in-memory test server implementing CONTRACTS v1.0.1. It does not
connect to PayPal, load .env, or need sandbox credentials.

From the repository root, with FastAPI and Uvicorn installed:

```powershell
$env:MOCK_CONTROL = "1"
python -m mock_paypal
```

The server listens at http://127.0.0.1:8099. Control routes are disabled
unless MOCK_CONTROL=1; tests can use create_app(control_enabled=True).

Implemented: OAuth client_credentials, bearer checks, PayPal error envelopes
and debug IDs, reset, clock GET/advance/set, and config GET/POST.
Payouts, seed, faults, disputes and webhooks belong to subsequent task cards.

OAuth accepts any nonempty mock client ID and secret via Basic auth.
Use dummy values only. Tokens expire after 32400 seconds of mock time;
reset revokes all tokens. The default clock starts at 2026-11-02T09:00:00Z.
Reset restores that time, default configuration, empty records and scheduled
events. A repeated reset seed reproduces resource IDs; tokens remain random.

Advance and set drain scheduled events in chronological order, including
events scheduled by callbacks. Equal-time events keep insertion order.
Callbacks may be synchronous or async. All timer deadlines use mock time.
Clock controls are serialized; backward moves and naive times are rejected.

All nine config flags match CONTRACTS section 5.7. Pending D4, D5, D8c,
D9, D11 and D12 behaviors remain flags. The pending D1 OAuth scope may be
overridden with create_app(oauth_scope=...). No sandbox claims are made.

Tests (also require pytest and httpx):
```powershell
python -m pytest -p no:cacheprovider tests/mock_paypal
python -m ruff check --no-cache mock_paypal tests/mock_paypal
```

Dependencies are the libraries already planned in C01: fastapi, uvicorn,
httpx and pytest; pydantic is supplied by FastAPI. Shared pyproject.toml,
import-linter contracts and the countersign package have not landed yet.
