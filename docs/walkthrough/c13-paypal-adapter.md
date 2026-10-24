# C13: the PayPal boundary

## 1. What this adds

The application now has its own PayPal adapter under `src/countersign/paypal/`.
It can read resources, verify webhook signatures, and send the four writes the
executor needs. These are application modules; they never import the day-one
scripts or the local mock. Tests can connect them to the mock in memory.

The adapter is implemented and tested against stubs and the local mock. It has
not been run against the PayPal sandbox. C09's recording checks remain pending
until Claude publishes the redacted recordings and their index.

## 2. Why it keeps money safe

Only the sandbox HTTPS origin or a configured localhost/127.0.0.1 mock is allowed.
Every request path and followed resource link is checked against that origin.
Redirects are never followed, OAuth is cached, and writes are never automatically
retried. Only the executor may import `paypal.write`; neutral response types live
in `paypal.http`, so the inbox need not import writes.

The result of a write has three possibilities:

- `Ok(resp)`: PayPal returned a successful HTTP status.
- `DefiniteError(resp)`: this first request received a definite client rejection.
- `Ambiguous(reason, resp, request_id, original_batch_link)`: reconciliation is
  needed. Network errors, server errors, in-progress responses and duplicate
  payout responses belong here. A client error on an UNKNOWN replay also stays
  ambiguous, because it says nothing definitive about the original request.

An original-batch link is checked against the configured origin and its resource
path; arbitrary query data is removed. The executor must GET the batch and verify
the payee and amount before adopting it. A duplicate error alone is not success.

## 3. How a request moves through the code

`PayPalHTTP` owns OAuth and the connection. `PayPalRead` sends GET requests for
payout batches/items, disputes, balances, trackers, orders, captures and webhook
events. It preserves PayPal's raw states and decimal strings; `response.amounts`
also exposes parsed `Money` objects, keyed by JSON pointer-like paths.

`PayPalWrite` takes a committed operation record. Its payload is an envelope:

```python
{
    "request_id": "cs-<line UUID>-a1",
    "body": {"sender_batch_header": {...}, "items": [...]},
    # Non-create operations instead carry their existing target:
    # "payout_item_id": "..." or "paypal_dispute_id": "..."
}
```

The row has an ID, the matching operation kind, state `IN_FLIGHT` or `UNKNOWN`,
and a hash of that entire payload. `canonical_payload_hash()` in `paypal.http`
uses UTF-8 JSON with sorted keys, compact separators, `ensure_ascii=False` and
`allow_nan=False`. Database hashes can be 32 bytes; API hashes are
`sha256:<hex>`. No call accepts a fresh replacement body. A payout contains one
item and stable batch/request/item keys. Its body is serialized deterministically
on every explicit replay.

The adapter can validate a supplied row's shape and fingerprint, but cannot prove
the database committed it. C19 must perform the write-ahead transaction, approval
consumption, shield recheck and 25-day replay-window check before calling it.
Run approvals cover a run manifest while each operation sends one payout item;
their hash and single-use approval scope must be reconciled by C19, not inferred
inside this adapter.

## 4. What gets saved, and where

This adapter saves no files and reads no `.env`. Settings come from the process
environment when the application's settings loader is called. Demo mode uses
`MOCK_PAYPAL_CLIENT_ID` and `MOCK_PAYPAL_CLIENT_SECRET`; live mode uses the
sandbox `PAYPAL_CLIENT_ID` and `PAYPAL_CLIENT_SECRET`. `PAYPAL_BASE_URL`,
`PAYPAL_WEBHOOK_ID`, `COUNTERSIGN_MODE`, `OWNER_TOKEN` and `DATABASE_URL` are
configured by the application. Configuration reprs are masked.

`HTTPResponse.body` is available only for trusted processing: it can contain real
payee or buyer emails. Its repr omits that body. Audit code must use
`HTTPResponse.safe_body`, which masks credentials, tokens, authorization assertions
and emails. The safe debug ID is captured separately. Errors contain fixed text
or exception type names, never the original network exception or private URL.

Webhook storage belongs to C14's inbox. `WebhookVerifier.verify(raw_body, headers)`
takes the exact original bytes and headers, without modifying either, and builds
PayPal's required postback object. Only an explicit successful verification result
is `SUCCESS`. Explicit `FAILURE` from a successful verification call is `FAILURE`;
network/server errors, malformed data or unknown statuses give `UNKNOWN`.
The verifier never fetches a certificate URL supplied by a webhook.

## 5. How correctness is decided

Unit tests cover successful writes, definite rejections, duplicate/in-progress
responses, unknown replays, lost responses, secret redaction, debug IDs, URL
guards, OAuth expiry/concurrency, exact money parsing, evidence formats and
signature results. Evidence defaults to multipart with a JSON `input` part;
accept-claim defaults to JSON. Pending sandbox behavior is centralized in
`PayPalBehavior`; there is no hidden format fallback or second write.

Contract tests use `httpx.ASGITransport` with the teammate's mock. They exercise
payout creation, replay, GET, cancellation, dispute evidence, staged adjudication,
owner claim acceptance, genuine and forged webhook signatures, and a response
dropped after PayPal accepted a payout. The latter creates exactly one item after
an explicit identical replay. Concurrent calls also create one item, with the
in-progress response classified as ambiguous. These results describe the mock,
not verified sandbox behavior.

## 6. How to run it

From this worktree, using its virtual environment:

```powershell
.venv\Scripts\python.exe -m pytest -q tests/unit/paypal tests/contract/test_paypal_adapter_mock.py
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\lint-imports.exe
```

These adapter tests need no database, Docker, tunnel or real credentials. The
recording contract placeholder is skipped with an explicit C09 reason.
