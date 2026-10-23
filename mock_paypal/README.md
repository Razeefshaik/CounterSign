# Mock PayPal - M01 and M02

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
Payout creation, batch/item lookup, cancellation and idempotency are implemented.
Seed, faults, dispute and webhook endpoints belong to subsequent cards.

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

## M02 payout behavior

The payout routes implement CONTRACTS section 5.2. Internally, balances and
payout amounts are integer USD minor units; API amounts are decimal strings.
Fees are zero, matching the contract examples. A reset starts with zero
available/withheld funds and no registered receivers. Until the broader seed
API is implemented, tests fund app.state.store.payouts.available_minor and
populate registered_receivers directly.

Creation debits available funds atomically. Processing uses
payout_processing_seconds: registered receivers reach SUCCESS; other
receivers reach UNCLAIMED. Batch SUCCESS means processing finished, so it can
contain UNCLAIMED items. Cancel permits only UNCLAIMED and defaults to RETURNED.
An unclaimed item's automatic return timer starts when it enters UNCLAIMED
and expires after 30 mock days. Amounts are credited once even if the same
state is applied repeatedly. Synthetic states have a domain transition helper;
their HTTP control endpoints and claim/cancel race controls belong to M03.

The unclaimed_counts_in_available flag defaults to false. Switching it
reprices existing UNCLAIMED items immediately; a later claim or return
accounts for the amount once.

Request-ID replay takes precedence over new body validation and sender keys.
Completed replays return 201 with the current batch header; changed bodies
are ignored by default. Concurrent overlapping duplicates return 409
DUPLICATE_REQUEST_ID. A new or absent request ID with a retained sender key
returns 400 USER_BUSINESS_ERROR and a link to the original batch.

Both keys expire exactly key_retention_days after creation (30 by default).
Replays do not extend expiry. Historical resources remain readable afterward.
An asyncio zero-duration checkpoint allows overlapping requests to observe
the in-progress response; it introduces no mock delay or wall-clock timer.
Clock/reset/config mutations and payout writes share a lock.

GET batch defaults to page 1, page_size 1000; pages accept up to 15000 items.
total_required=true includes total_items/total_pages. HATEOAS links use
the local request base URL. No outbound API request is made.

Pending D3/D5/D6/D7 response defaults can be overridden without changing the
nine contract config flags:

    from mock_paypal.app import create_app
    from mock_paypal.payouts import PayoutBehavior

    app = create_app(
        control_enabled=True,
        payout_behavior=PayoutBehavior(
            create_includes_items=False,
            cancel_item_status="RETURNED",
            insufficient_funds_status=422,
            concurrent_duplicate_status=409,
            concurrent_duplicate_name="DUPLICATE_REQUEST_ID",
            duplicate_batch_status=400,
            duplicate_batch_name="USER_BUSINESS_ERROR",
            duplicate_batch_issue="DUPLICATE_SENDER_BATCH_ID",
            replay_current_header=True,
            replay_original_body_wins=True,
        ),
    )

These defaults are contract fixtures, not sandbox-verified findings.
No dependencies beyond M01/C01's approved libraries were added.
