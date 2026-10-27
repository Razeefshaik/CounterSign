# Mock PayPal - M01 through M09

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
Seed, faults, dispute resources and test-signed webhook endpoints are implemented.

OAuth accepts any nonempty mock client ID and secret via Basic auth.
Use dummy values only. Tokens expire after 32400 seconds of mock time;
reset revokes all tokens. The default clock starts at 2026-11-02T09:00:00Z.
Reset restores that time, default configuration, empty records and scheduled
events. A repeated reset seed reproduces resource IDs; tokens remain random.

Advance and set drain scheduled events in chronological order, including
events scheduled by callbacks. Equal-time events keep insertion order.
Callbacks may be synchronous or async. All timer deadlines use mock time.
Clock controls are serialized; backward moves and naive times are rejected.

Config flags start from CONTRACTS section 5.7, plus duplicate_link_host, with
observed defaults updated from C09's published recordings and owner instructions:
dispute_hold=false, evidence_allowed_stages=null (status and actual action link
decide permission), cancel_emits=["RETURNED"]. Legacy synthetic alternatives
remain explicit options. OAuth includes the observed disputes/create scope;
this scope does not authorize dispute creation, which returns the recorded 400.
The scope string can be overridden with create_app(oauth_scope=...).

Tests (also require pytest and httpx):
```powershell
python -m pytest -p no:cacheprovider tests/mock_paypal
python -m ruff check --no-cache mock_paypal tests/mock_paypal
```

Dependencies are the approved dev libraries: fastapi, uvicorn, httpx and pytest;
pydantic is supplied by FastAPI. No additional dependency is required.

## M02 payout behavior

The payout routes implement CONTRACTS section 5.2. Internally, balances and
payout amounts are integer USD minor units; API amounts are decimal strings.
Generated payout fees remain zero, matching the contract examples. Recorded
historical item fees are preserved when seeded. The observed $0.25 fee on a
$1 payout does not establish a general pricing formula; generated fees are a
simulation assumption, not sandbox-equivalent accounting. A reset starts with
zero available/withheld funds and no registered receivers; /_mock/seed funds it.

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

Response alternatives remain configurable through
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

D7 method/path-specific fixtures in fixtures/d7_responses.json come from the
published redacted C09 recordings; each stores its source path. Literal negative
IDs on reads/cancels and payout notes on create select the recorded status/body.
For example ERRPYO015 returns a CLOSED_MARKET batch on GET batch but 404 on GET
item, ERRPYO047 is UNCLAIMED, and ERRPYO039 returns 400. Debug IDs and API links
are localized. Explicit negative_values overrides remain available for injected
test faults; unmatched combinations retain legacy synthetic fixtures.


## M04 resource fixtures

The seed endpoint loads balances, receiver registry, orders, captures,
trackers, raw dispute documents and historical payout items. Seed balances
already include existing holds and payouts; importing them never debits twice.
Full raw captures retain optional metadata; historical reporting transactions
can be seeded under transactions and remain subject to the mock reporting lag.
Invalid seed data is staged before replacement. Resources and transactions
remain in memory. No deduplication uses transaction ID alone.

Balance snapshots refresh on the mock clock; actual funds change immediately.
Reporting transactions remain hidden until transactions_lag_seconds elapses.
Evidence and accept-claim accept JSON or multipart with one input part holding
JSON, parsed with Python's standard email parser. Malformed or duplicate input
parts are rejected before mutation. D10 recorded successful multipart evidence
and JSON accept-claim; support for both formats on both endpoints remains a
local compatibility option. No multipart dependency is required.
Observed dispute links are preserved, with PayPal API hosts localized. Evidence
needs WAITING_FOR_SELLER_RESPONSE and a provide_evidence link; a lifecycle stage
never supplies an absent observed permission. Evidence/accept-claim return a
detail link acknowledgement; adjudicate returns a bare self link. GET reads the
updated dispute. Sandbox adjudication controls require UNDER_REVIEW.
Open disputes freeze captures at PENDING without changing withheld_balance.
Buyer refunds use available funds; seller wins/buyer-close do not credit an
imaginary hold. Synthetic hold experiments can opt in via dispute_hold and
ResourceBehavior.refund_uses_withheld. Recorded outcome spellings are retained.


Balance history is capped at the latest 256 snapshots per mock instance (32 days
at the default three-hour refresh interval). Older snapshots are evicted; an
`as_of_time` before the retained history returns `404 RESOURCE_NOT_FOUND`.
The refresh delay and all timestamps still use the mock clock. Reseeding/reset
replaces the history, so repeated demo runs do not accumulate snapshots.

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

Registration/PATCH, event listing, resend and simulate-event use the observed
acknowledgement/event shapes. Simulator events have valid mock signatures but
do not move funds; generated activity stays labelled as simulated.

## M09 recording comparison

tests/mock_paypal/test_recording_replay.py inventories all 1,041 published HTTP
exchanges across D1 through D13. It replays 999 supported API requests through
the in-process FastAPI transport, requiring every recorded response field/type,
HTTP status and error name. Historical resources are seeded from observations;
IDs, timestamps, callback URLs and signatures are locally generated. These are
shape checks, not byte equality or evidence of a live PayPal call. Independent
transition tests verify balances, refunds, capture states and permissions.

The remaining 42 entries are explicitly counted: 34 transport failures without
HTTP responses, one non-JSON edge 503, and seven buyer checkout setup writes
outside CONTRACTS section 5. Connection-loss behavior is covered separately by
the local TCP lost-response test; no fixture coverage is silently skipped.

Balance refresh remains conservatively three mock hours (observed 1.4–2.4 hour
lag); transaction visibility and webhook delivery delays remain configurable
simulation defaults. This work retains approved CONTRACTS 1.0.3: M09's task-card
reference to 1.1 is stale. Shared docs/contracts are left for their owner to update.
No Gemini key or model call is required for this mock-only card.

## M14 forced payout outcomes

`POST /_mock/payout-outcomes` with `{"receiver", "to", "nth"?, "count"?, "id"?, "errors"?}`
forces the processing result of payout items created later for that receiver,
because a scenario cannot know generated item IDs. `to` is SUCCESS, UNCLAIMED,
ONHOLD, BLOCKED, FAILED or PENDING; `nth`/`count` count only items processed for
that receiver. PENDING leaves the item stuck and its batch PROCESSING (no batch
SUCCESS event). Forced items are always `synthesised` in ground truth (F13);
other state changes, balances and item webhooks reuse the normal transition.
`GET /_mock/payout-outcomes` lists rules; reset clears them.

Delay and timeout request faults wait on the mock clock. The in-process eval
runner advances that clock only between steps, so those two faults cannot be
used in library scenarios run in-process; scenarios use drop_response,
connection_reset, http_error and recorded test values instead.
