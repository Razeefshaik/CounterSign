# C21: one disputed share and one safely recovered payout

## 1. What this proves

This slice connects the existing database, owner approval, shield, executor,
webhook inbox, poller and incident lifecycle. Maker A and Maker B each have a
$2 order. Their 90% shares are $1.80 each. A staged buyer dispute reserves A's
share, so the run contains only B's $1.80 payment.

The first payout response is deliberately lost after sending. Recovery uses the
same saved operation, body and keys. The final proof requires one payout item,
B's share settled once, A's share still reserved, and the **payout incident**
resolved by reconciliation. A's dispute and its incident remain open.

The automated tests use the local mock, fake credentials and isolated PostgreSQL
schemas. The sandbox script is implemented and tested with stubbed HTTP; the
owner and Claude must still run it against PayPal. These are separate results.

## 2. What you do in the browser

The sandbox script pauses for three kinds of human action:

1. Sign in as buyer-1 and approve each of the two $2 orders at its printed sandbox
   approval URL. Return to the terminal and press Enter after each approval.
2. In the sandbox Resolution Center, open an item-not-received dispute on the
   printed **Maker A capture ID**. This is a staged action. Enter the resulting
   dispute ID. On resume, enter the existing ID instead of opening another case.
3. Review the printed run: B receives $1.80 and A's $1.80 stays reserved. Check the
   exact payload hash. Press Enter to send that hash to the real owner approval
   endpoint. The script supplies the owner token without displaying it.

Maker emails are trusted setup facts: the owner must supply two distinct,
verified sandbox recipients. This slice does not verify ownership of an email
through PayPal. It does not ask the model to approve a payment.

## 3. What the script does

```mermaid
sequenceDiagram
    participant Owner
    participant Slice as Slice CLI
    participant Engine
    participant DB as PostgreSQL
    participant PP as PayPal sandbox or mock
    participant App as Webhook and owner app
    Slice->>PP: Create and capture two approved orders
    Slice->>Engine: Seed two makers and order shares
    Owner->>PP: Stage dispute on A's capture
    Slice->>Engine: Tick and plan weekly run
    Engine->>DB: Reserve A; plan B's $1.80 line
    Owner->>App: Approve exact payload hash
    App->>DB: Save bound unused approval
    Slice->>Engine: Tick executor
    Engine->>DB: Commit write-ahead operation
    Engine->>PP: Payout POST with saved keys
    PP--xEngine: Response deliberately discarded
    Engine->>DB: Record UNKNOWN
    Slice->>Engine: Tick while staged replay pause remains
    Engine->>DB: Discover open payout incident
    Slice->>Engine: Release pause and tick recovery
    Engine->>PP: Identical replay, then original batch and item GETs
    PP->>App: Signed payout webhook
    App->>DB: Durably store exact delivery
    App-->>PP: 200 after durable storage
    Slice->>Engine: Verify/process inbox and reconcile GETs
    Engine->>DB: Settle B once; resolve payout incident
    Slice->>DB: Read exact event type and run resource ID
    DB-->>Slice: Signature SUCCESS and status PROCESSED
```

Only the CLI advances the worker during this experiment. Its receiver runs the
existing app with background workers disabled. A script-only transport loses the
first payout response; a second script-only gate briefly prevents replay POSTs
from being sent until the real lifecycle has discovered the UNKNOWN payout
incident. It then releases recovery to the unchanged executor. No database state
is forced to manufacture that incident or its closure.

The mock uses its `drop_response` fault with `after_commit: true`. Sandbox uses a
transport that forwards the first payout POST, closes its response without
reading it, then raises a read timeout. C19b owns all financial recovery. The
real D6 duplicate shape is a 400 `USER_BUSINESS_ERROR` with the original batch
link in `details[].link`; a 400 replay does not prove the first payment failed.

## 4. What gets saved and how resume works

The default evidence directory is `recordings/slice/<UTC-date>/`:

| File | Purpose |
| --- | --- |
| `state.json` | Atomic, fsynced checkpoints, step status, saved resource IDs and fault markers |
| `run.jsonl` | Redacted append-only steps, order/capture/dispute/batch/item IDs and final proof |
| `.lock` | Prevents two CLI processes from running the same checkpoint concurrently |

Writes are checkpointed before sending. A completed step is skipped. Interrupted
order setup uses the existing sandbox harness helpers and original keys;
financial recovery stays with the persisted executor operation. The mock's
administrative seed, dispute and fault controls stop for review after an
unconfirmed control write rather than guessing whether it happened.

Resume with the same command, UTC date, evidence root, app URL, database,
credentials, webhook ID and maker identities. The saved identity fingerprint
rejects changed run settings. Keep the receiver and tunnel alive. Do not delete
the checkpoint, reset the database or use a new date to recover an UNKNOWN write.
If an approval expires or becomes stale before execution, stop for owner review.
An unseeded run that crosses into the next payout week also stops for review,
because seed due dates follow the current clock. A completed checkpoint is
rechecked against a fresh financial snapshot before success is reported.

Settings load only at runtime. Public evidence goes through the existing
redactor; tokens, credentials and real emails must not appear. Resource IDs are
the evidence intended for the video. Review redacted evidence before publishing.

## 5. How success is decided and tested

The positive test checks the real owner endpoint, one accepted payout item in
mock ground truth, one settled obligation and one success journal. It also checks
that A remains reserved and that the payout incident gets one reconciliation
closure audit. A second replay variant uses `MockTransport` to supply the real
D6 nested-link error because the current mock returns 201 for an identical
replay. Aman can align that mock behavior separately.

The negative test delivers a genuine signed SUCCESS webhook while the payout
item GET still says PENDING. The payout incident must stay open, B must stay
IN_FLIGHT and no success journal may be posted. When the GET agrees, resuming
settles the original payment without a second approval or payout item.

The owner approved one narrow exception to the public-facade rule: the script and
test may read `webhook_inbox` with a parameterised query matching **event_type
and resource_id** from this run. Completion requires an exact payout item or
batch event with `signature='SUCCESS'` and `status='PROCESSED'`. An unrelated
event or a processed-event count cannot satisfy it. No inbox writes, new Engine
methods or contract changes are added.

Other tests cover durable resume, the per-run lock, redaction, the unread
lost-response fault, order/capture reuse and exact webhook registration. Tests
make no real PayPal calls. The dashboard's HTTP financial pages remain fixtures
until M15; the script's Engine snapshot and inbox evidence establish this result.

The focused C21 file passed all 25 tests against the local PostgreSQL 16 service:
four integration scenarios and 21 script checks. Inbox regression tests also
check item and batch identity mismatches, unknown events, and that processing a
confirmed payout webhook changes no financial rows or ledger postings.

## 6. How the owner runs it

Use the `CounterSign-c21` worktree. Before running, prepare a **dedicated empty
PostgreSQL 16 database with the current migrations applied**. This script never
migrates or resets a database. Start Docker/PostgreSQL and arrange enough sandbox
seller funds for the payout, fees and possible dispute hold.

In the owner's existing runtime environment file, configure these names privately:

| Variable | Required setting |
| --- | --- |
| `DATABASE_URL` | Dedicated migrated slice database |
| `COUNTERSIGN_MODE` | `live` (this selects the PayPal sandbox, never production) |
| `PAYPAL_BASE_URL` | `https://api-m.sandbox.paypal.com` |
| `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET` | The same sandbox seller app used for the orders and payout |
| `PAYPAL_WEBHOOK_ID` | Registration for the app's current tunnel callback |
| `OWNER_TOKEN` | Owner endpoint token |
| `SBX_MAKER_A_EMAIL`, `SBX_MAKER_B_EMAIL` | Two distinct owner-verified sandbox recipients |

Do not paste values into chat or print the environment file. Existing process
environment values take precedence over the runtime file, so both terminals must
use the same settings. The example below names the file without reading it.

**Terminal 1, receiver and owner API:**

```powershell
Set-Location 'S:\StudyResource\TechBoooo\Hackathons\PayPalDevPost\CounterSign-c21'
$env:PYTHONPATH = 'src'
$sliceEnvFile = 'S:\StudyResource\TechBoooo\Hackathons\PayPalDevPost\CounterSign\.env'
..\CounterSign-db\.venv\Scripts\python.exe -m scripts.slice_e2e --target sandbox --serve --port 8000 --env-file $sliceEnvFile
```

This command selects the Windows event loop needed by psycopg, binds to
127.0.0.1 and disables background workers. Keep it running. Do not start the
normal worker or another executor on this database during the experiment.

**Terminal 2, tunnel:**

```powershell
ngrok http 8000
```

Register the actual HTTPS origin plus `/webhooks/paypal` for the **app**, not the
day-one catcher. Subscribe to at least `CUSTOMER.DISPUTE.CREATED` and
`PAYMENT.PAYOUTS-ITEM.SUCCEEDED` (the full existing event subscription also works).
Set that registration's `PAYPAL_WEBHOOK_ID` privately, then restart the receiver
so both processes use it. The CLI checks the exact ID, callback URL and event
subscription before creating orders; it does not register a webhook itself.

**Terminal 3, slice:** replace the example tunnel origin with the actual one.

```powershell
Set-Location 'S:\StudyResource\TechBoooo\Hackathons\PayPalDevPost\CounterSign-c21'
$env:PYTHONPATH = 'src'
$sliceEnvFile = 'S:\StudyResource\TechBoooo\Hackathons\PayPalDevPost\CounterSign\.env'
$sliceDate = [DateTime]::UtcNow.ToString('yyyy-MM-dd')
..\CounterSign-db\.venv\Scripts\python.exe -m scripts.slice_e2e --target sandbox --app-url http://127.0.0.1:8000 --tunnel-url https://your-current-tunnel.ngrok-free.app --env-file $sliceEnvFile --date $sliceDate
```

Follow the browser steps in section 2. Leave the receiver and tunnel running
until the script reports completion. Polling waits 30 seconds between sandbox
observations, with 120 attempts per waiting stage by default. A timeout means
the proof is pending; preserve the checkpoint and rerun with the original date.
If the tunnel origin changes, restore the registration and receiver consistently
before resuming; do not create new financial work to bypass the pending proof.

For automated development validation, use the local PostgreSQL URL without
loading the sandbox environment:

```powershell
$env:PYTHONPATH = 'src'
$env:COUNTERSIGN_TEST_DATABASE_URL = 'postgresql://countersign:countersign-dev@127.0.0.1:55432/countersign'
..\CounterSign-db\.venv\Scripts\python.exe -m pytest -q tests/integration/test_slice_mock.py
..\CounterSign-db\.venv\Scripts\ruff.exe check .
..\CounterSign-db\.venv\Scripts\lint-imports.exe
```

The displayed database password is the repository's local-dev default. Database
tests skip when the test URL is unset. A standalone `--target mock` script run
also needs a fresh local mock with controls enabled, a registered callback and
the receiver configured for demo/mock settings; the automated test supplies
those pieces in process.
