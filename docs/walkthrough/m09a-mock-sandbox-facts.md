# M09a: make the local mock match the first sandbox findings

The mock now follows CONTRACTS 1.0.3 and the owner's D5/D6 findings. It uses
fake money and never connects to PayPal or loads sandbox credentials.

## Recovering a payout after losing its response

Suppose the first payout POST creates a batch, but the connection drops before
the caller receives its ID. Repeating the same request now returns **400
USER_BUSINESS_ERROR**, rather than another 201. This error means the sender
batch already exists; it does not mean the original payment failed.

The caller finds the original batch URL in `details[0].link[0].href`, GETs
that batch, and checks its recipient and amount before adopting it. Top-level
`links` is empty. The tests prove there is still only one payout item and one
debit, including over a real local TCP connection with `drop_response` and
`after_commit: true`.

Exact retries, changed payment bodies with the same sender batch ID, new or
absent request IDs, and concurrent duplicates all receive the same error.
Concurrent creation is serialized so exactly one request receives 201.
`PayPal-Request-Id` alone does not deduplicate a new sender batch ID. The
sender batch key expires after the configured retention window, without
retries extending that window.

## Following links locally

Duplicate links use the incoming request's base URL by default. This lets
core follow them against the local mock. To reproduce the recorded host:

```json
POST /_mock/config
{"duplicate_link_host": "sandbox"}
```

The resulting link starts with `https://api.sandbox.paypal.com`. This setting
changes the response text only. Core should deliberately retain UNKNOWN when
that host is unusable with its configured mock connection. Set the flag back
to `"mock"` to restore local links; reset also restores this default.

## Cancelling and submitting dispute actions

Cancel an UNCLAIMED payout item with an empty-body POST. Its response already
says RETURNED, and a later item GET agrees. Cancelling it twice returns 400
ITEM_ALREADY_CANCELLED. Cancelling a SUCCESS item returns 400
ITEM_INCORRECT_STATUS. No second credit is made.

Provide-evidence and accept-claim accept JSON or multipart with a single
`input` part containing JSON. Tests cover both an ordinary form part and a
file part. Missing, duplicated, malformed or non-object input is rejected
before evidence or funds change. Accepting both formats in the mock is a
temporary compatibility choice while the real sandbox format awaits D10.

The meta fixture, dashboard footer, eval schema and tests advertise 1.0.3.
CONTRACTS.md itself is unchanged by this task.

## Verification

Run `python -m pytest tests/mock_paypal tests/eval tests/dashboard`,
`python -m pytest`, `ruff check .`, `lint-imports`, and
`node --test web/tests/*.test.mjs`.

On this branch: 297 mate Python tests and 20 web tests pass. Ruff passes and
all three import contracts are kept. The full Python suite reports 1,933
passed, 244 skipped and the two core failures below. Database integration
tests are skipped without a configured test database; this run is not a
claim of database integration coverage.

Two core-owned tests in `tests/contract/test_paypal_adapter_mock.py` still
assert that payout replay returns `Ok`. Contract 1.0.3 instead produces
`Ambiguous` with `original_batch_link`, followed by GET-based recovery. Core
must update `test_mock_payout_read_cancel_replay_and_balances` and
`test_mock_lost_response_after_commit_identical_replay_one_item`. M09a leaves
these files untouched under the folder ownership rules.
