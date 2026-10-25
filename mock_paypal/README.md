# Mock PayPal - M01 through M09a

Local, in-memory test server implementing CONTRACTS v1.0.3. It does not
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

Config flags follow CONTRACTS section 5.7, plus duplicate_link_host. Pending D4, D8c,
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

D6 duplicate detection uses sender_batch_id only. Every duplicate within the
retention window returns 400 USER_BUSINESS_ERROR, including identical retries,
changed bodies, new request IDs and concurrent losers. The original batch link
is in details[].link; top-level links is empty. A request ID with a new sender
batch ID creates a new payout. Sender keys expire exactly key_retention_days
after creation (30 by default); retries do not extend expiry. Historical batches
remain readable. Clock/reset/config mutations and payout writes share a lock.

Duplicate links use the request's local base URL by default. Set
`POST /_mock/config` to `{"duplicate_link_host": "sandbox"}` to reproduce the
recorded `https://api.sandbox.paypal.com` host, or `"mock"` to restore local
links. The sandbox option changes response text only; the mock makes no
outbound PayPal calls. Tests should follow the default local links.

GET batch defaults to page 1, page_size 1000; pages accept up to 15000 items.
total_required=true includes total_items/total_pages. HATEOAS links use
the local request base URL. No outbound API request is made.

Pending D3 and D7 response defaults remain configurable through
`create_app(payout_behavior=PayoutBehavior(create_includes_items=False,
insufficient_funds_status=422))`. Confirmed D5 cancellation always returns
RETURNED, and D6 duplicates always return 400. No dependencies were added.

## M03 faults and ground truth

Request faults match method, regex path, nth and count. Delay/timeout waits
resolve only when the mock clock advances. A dropped response sends an
incomplete HTTP body and closes the connection; a real TCP acceptance test
checks that the client receives a protocol error while the committed payout
survives; an identical replay returns 400 with a link to that single batch.
Fault logs retain keys and statuses, never auth headers.
Transition/claim controls include the claim-during-next-cancel race.

D7 names follow the project fixture codes and the official reference:
https://developer.paypal.com/api/payouts/test-go-live
HTTP codes remain configurable via create_app(negative_values=...) pending
recordings. Legacy ERRPYO015 is supported as the contract's item fixture.
These are mock fixtures, not observed sandbox responses.


## M04 resource fixtures

The seed endpoint loads balances, receiver registry, orders, captures,
trackers, raw dispute documents and historical payout items. Seed balances
already include existing holds and payouts; importing them never debits twice.
Invalid seed data is staged before replacement. Resources and transactions
remain in memory. No deduplication uses transaction ID alone.

Balance snapshots refresh on the mock clock; actual funds change immediately.
Reporting transactions remain hidden until transactions_lag_seconds elapses.
Evidence and accept-claim accept JSON or multipart with one input part holding
JSON, parsed with Python's standard email parser. Malformed or duplicate input
parts are rejected before mutation. The real sandbox format remains pending
D10; accepting both locally makes no claim that both work there. No multipart
dependency is required.
Only status/stage-allowed actions receive HATEOAS links. Held refunds consume
withheld funds by default, and seller wins/buyer-close release holds once.
ResourceBehavior exposes pending D8/D9/D10 choices through create_app.


## M05 webhook signing and delivery

Events are signed with the explicitly PUBLIC mock-only RSA fixture in
test_signing_fixture.py. It contains no PayPal keys or real credentials.
The implementation uses standard-library SHA256/PKCS1-v1_5 with this fixed
test key, with an independent OpenSSL certificate verification test.
OpenSSL generated the fixture once; the server does not require it at runtime.

Only local HTTP(S) callback URLs are accepted, and redirects are disabled.
Delivery, duplicate, drop, delay and reorder faults run through mock-clock
events. Duplicate copies is the total number of deliveries; delay adds to
the normal delivery delay. Reorder holds an event until its target is emitted.
The verify API binds event ID, stored raw bytes, webhook ID and transmission
headers. Authentic replays still verify; inbox deduplication belongs to core.
