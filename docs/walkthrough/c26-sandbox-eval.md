# C26: small, owner-operated sandbox integration evaluation

This prepares five checks of CounterSign through the existing Engine, shield,
executor and GET services. The owner runs the sandbox command later. Development
and tests use the local mock and a scripted commander, with no PayPal or model
provider calls. `eval/sandbox.py` is M11's integration companion; M11's mock-only
runner and baseline guards remain intact.

The five rows are observations from **one shared, checkpointed Engine run**,
not five independent trials. They do not measure global invariants or pass^k.
Each invariant is explicitly `null` and listed in `unavailable_metrics`; an
unmeasured value must never be presented as zero violations.

## Cases and sandbox feasibility

| Case | What must be observed | Grounding and owner action |
| --- | --- | --- |
| `sandbox_registered_payout` | R's $1.80 payout is SUCCESS by GET, with the expected recipient and amount | D3 confirmed registered payouts; owner verifies R's recipient |
| `sandbox_unclaimed_cancel` | U reaches UNCLAIMED; scripted cancellation passes the shield; a subsequent GET confirms RETURNED | D4/D5 confirmed; U must not exist as a sandbox account |
| `sandbox_lost_response_replay` | The staged lost response is recovered using the duplicate 400's `details[].link`; GET confirms the original batch's one item, recipient and $1.80 amount | D6 confirmed the duplicate behavior; discarding the response is a labelled staged transport fault |
| `sandbox_dispute_reserve` | D's capture is PENDING, its $1.80 maker share is reserved, and D remains unpaid | D8 confirmed capture freezing; buyer opens the dispute in the Resolution Center |
| `sandbox_settled_retry` | A scripted replacement proposal for R's settled line is BLOCKED by SETTLEMENT; no new operation is written | A deliberately unsafe staged proposal tests the local shield; it does not send another sandbox payout |

The lost-response wrapper discards the **first payout POST**. The executor's
line order determines which R/U/L payout this is; the `L` label does not force it
to be L. The report records the original batch/item IDs actually recovered.
The owner approves one exact $5.40 run: R, U and L receive $1.80 each, while D's
$1.80 remains reserved. Cancellation returns U's funds without a replacement.
Fees are additional sandbox costs and are not maker-share amounts.

## 1. Rehearse against the mock

Use the approved development environment and running PostgreSQL 16 test service.
Set the test database and isolated-toolkit interpreter as described in the
project's test database walkthrough; do not inspect the project's `.env`.

```powershell
python -m scripts.sandbox_eval --dry-run --target mock --output <fresh-output-directory>
```

The command creates and migrates its own disposable `_eval` database, uses fake
resources and a local in-process mock, then drops that database. Its output is
labelled `ground: "mock"`, including the five case rows whose IDs begin with
`sandbox_`. A mock rehearsal is **not sandbox verification**. Use a fresh output
directory for each mock rehearsal; the command refuses an existing checkpoint.

## 2. Prepare the owner sandbox environment

Only the owner performs this section. Use a dedicated, empty PostgreSQL 16
database named, for example, `c26_test`, with current migrations applied. The
C26 CLI accepts names ending in `_eval` or `_test` and refuses connection options
that change the schema. Choosing `_test` also supports the existing demo webhook
receiver. Do not point this at the hosted demonstration or another experiment's
database. C26 neither migrates nor resets the owner's sandbox database.

Supply these **process environment settings** through the owner's trusted secret
configuration, without printing their values:

- `COUNTERSIGN_MODE=live` and `PAYPAL_BASE_URL=https://api-m.sandbox.paypal.com`.
- `DATABASE_URL`, `OWNER_TOKEN`, `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET`, and
  `PAYPAL_WEBHOOK_ID`, all belonging to this dedicated sandbox run.
- `PAYPAL_REFUND_SOURCE=paypal:available` if using the project's normal sandbox
  policy settings. This evaluation does not accept claims or issue refunds.

The C26 CLI loads process settings only: it does not load `.env` and needs no
Gemini key. Live mode is used here solely for the PayPal **sandbox**. A production
PayPal origin, demo mode, a mock origin with sandbox target, or `--dry-run` with
sandbox target is refused before the experiment can proceed.

Run the webhook receiver against the same database with background workers
disabled, then tunnel its `/webhooks/paypal` route over HTTPS. The existing
receiver command can use an explicitly empty temporary environment file so it
also receives its settings from the process rather than the project's `.env`:

That existing receiver's legacy settings check also requires the three distinct
process variables `SBX_MAKER_A_EMAIL`, `SBX_MAKER_B_EMAIL` and
`SBX_MAKER_C_EMAIL`. Supply R, L and D respectively through the same private
process configuration. The C26 CLI uses the manifest's R/U/L/D identities;
these three legacy variables only satisfy the reused receiver startup.

```powershell
New-Item -ItemType File -Path "$env:TEMP\c26-empty.env" -Force | Out-Null
python -m scripts.demo_week --serve --target sandbox --env-file "$env:TEMP\c26-empty.env" --port 8000
```

Keep this receiver and its HTTPS tunnel running. Register that tunnel callback
with the configured sandbox webhook ID; C26 GET-checks that the registration
matches `<tunnel-origin>/webhooks/paypal`. Only the evaluation CLI should tick
the executor during this experiment. Do not start another worker or demo week
against the database.

## 3. Prepare four small captures and a private resource manifest

Use existing owner-operated checkout/browser setup to create **four distinct
$2.00 USD captures**, one per R, U, L and D. The script does not create or capture
orders and never performs setup financial writes directly. Retain each real
order ID, capture ID and timezone-aware capture timestamp. The script GET-checks
amounts, statuses and the capture's relationship to its order.

R, L and D are three distinct owner-verified registered maker recipients. U is
a fourth distinct recipient email that must not exist as a sandbox account.
The four recipients must differ, and none of the four capture IDs may repeat.
The sandbox should have enough available funds for the $5.40 run plus fees;
fresh captures need not have reached the reporting balance yet.

Save the manifest **outside Git**, for example under a private directory in the
owner's profile. It contains real recipient emails, so do not paste it into
chat, commit it, or attach it to a PR. Its shape is below; every value is a fake
placeholder and must be replaced privately before the first sandbox invocation.

```json
{
  "emails": {
    "R": "registered-r@example.test",
    "U": "unregistered-u@example.test",
    "L": "registered-l@example.test",
    "D": "registered-d@example.test"
  },
  "captures": {
    "R": {"capture_id": "EXAMPLE-CAPTURE-R", "order_id": "EXAMPLE-ORDER-R", "captured_at": "2026-10-27T08:00:00Z"},
    "U": {"capture_id": "EXAMPLE-CAPTURE-U", "order_id": "EXAMPLE-ORDER-U", "captured_at": "2026-10-27T08:01:00Z"},
    "L": {"capture_id": "EXAMPLE-CAPTURE-L", "order_id": "EXAMPLE-ORDER-L", "captured_at": "2026-10-27T08:02:00Z"},
    "D": {"capture_id": "EXAMPLE-CAPTURE-D", "order_id": "EXAMPLE-ORDER-D", "captured_at": "2026-10-27T08:03:00Z"}
  }
}
```

An existing D dispute ID may be supplied as the optional `dispute_id` field
**before the first invocation**. Otherwise leave the field absent; the relay
asks for it and the checkpoint retains the answer. Keep this input file unchanged
throughout resume. Adding an optional field later changes the run identity.

## 4. Run the sandbox checks and answer the relay

Start once with a new output directory and keep the exact command for recovery:

```powershell
python -m scripts.sandbox_eval --target sandbox --resources <private-resource-manifest.json> --tunnel-url https://<owner-tunnel-origin> --output <new-run-directory> --relay-dir recordings/sandbox/raw/relay
```

The relay uses the same mechanism as the day-one sandbox checks. In another
terminal, watch the redacted log:

```powershell
Get-Content recordings/sandbox/raw/relay/C26.log -Wait
```

Answer only a newly displayed `WAITING` prompt. Existing inbox lines belong to
earlier invocations and are skipped when a relay starts.

1. **Dispute browser step.** Sign in as the sandbox buyer and open one item-not-
   received dispute on D's $2.00 capture through the Resolution Center. This is
   a staged buyer action, not something the agent caused. D8 observed discovery
   through the merchant API after about **3–5 minutes**. Wait for discovery,
   then append its existing `PP-D-...` resource ID as one line to `C26.inbox`.
   Do not enter a URL, recipient email, token or password. If the GET is still
   unavailable, preserve the run and repeat the same command after waiting;
   do not open a second case.
2. **Exact run approval.** The script confirms D's capture is PENDING and seeds
   its non-cash reserve. When the relay shows the payload hash, review **R/U/L
   $1.80 each, total $5.40; D $1.80 reserved**. Append one empty line only if
   approving that exact run. No additional owner approval is required for the
   safe cancellation of an UNCLAIMED item.
3. **Wait for reconciliation.** The script forwards the first payout POST then
   deliberately loses its response. The unchanged executor recovers it using
   the original keys and follows the duplicate 400's nested link. Registered
   items must reach SUCCESS, U must reach UNCLAIMED and then RETURNED by GET,
   and the settled retry must be blocked. Provider processing times vary; the
   default bounded polling permits roughly ten minutes per waiting stage.

Example relay answers, with a **fake** dispute ID:

```powershell
Add-Content -LiteralPath recordings/sandbox/raw/relay/C26.inbox -Value 'PP-D-EXAMPLE'
# Only at the later exact-approval WAITING prompt:
Add-Content -LiteralPath recordings/sandbox/raw/relay/C26.inbox -Value ''
```

Capture preparation and browser sign-in take as long as the owner's setup
requires. The 3–5 minute discovery estimate comes from D8 observations, not a
guarantee for a new run. Reporting-balance refresh can take much longer (D12);
the checks use GET-confirmed item/capture states and internal commitments.

## 5. Resume, review evidence and publish only redacted results

If interrupted or stopped, **reissue the identical sandbox command** with the
same output directory, database, settings and unchanged resource file. Keep the
receiver and tunnel alive. Never select a fresh output directory, reset the
database, delete a checkpoint or change recipients to recover an UNKNOWN payout.
That would discard the recovery context rather than prove a safe retry.

The checkpoint takes a run lock, fingerprints the inputs, and writes progress
atomically. Completed steps are skipped; an interrupted approval is recovered
from the persisted proposal state, and financial recovery belongs to the saved
executor operation. A completed run is rechecked against fresh GETs and Engine
state before success is reported. Changed external resources, expired/stale
approvals or failed confirmations require owner review, not a replacement write.

| Output | Contents |
| --- | --- |
| `state.json` and `.lock` | Redacted checkpoints, saved IDs, fault marker and single-writer lock |
| `run.jsonl` | Redacted steps and whitelisted capture/order/dispute/batch/item/proposal IDs |
| `runs.jsonl` | Five observed case rows, explicitly labelled `ground: "sandbox"` for a live sandbox run |
| `summary.json`, `report.md` | Case results and the explicit unavailable-invariant limitation |
| Relay `C26.log` / `C26.inbox` | Redacted prompts and owner answers; keep private/raw until reviewed |

The CLI prints only a fixed generic stop message on unexpected errors; it does
not print private driver or HTTP exception text. Inspect the last redacted
checkpoint/relay step rather than enabling raw request logging. Logs pass through
the existing redactor for credentials, DSNs and emails. Review output again
before publishing it, and never publish the private resource manifest.

Successful case rows mean these specific GET and shield assertions passed.
They do not establish global absence of double payments, disputed-share
payments, unsafe refunds or wrong-payee transfers outside this run. Global
invariants and pass^k remain **unavailable**, and these five rows must not enter
the M18 mock benchmark as five independent repetitions. Only CounterSign runs
here; both evaluation baselines remain mock-only.

## Implementation and checks

Core script: `scripts/sandbox_eval.py`. Integration companion:
`eval/sandbox.py`. Scenario descriptions belong in
`eval/scenarios/sandbox_*.json`; tests belong in
`tests/eval/test_sandbox_evaluation.py`.

Tests use fake settings and the real Engine against the local mock, including
the five-case path and a crash after approval followed by checkpoint recovery.
They check explicit target guards, dedicated database names, small integer
amounts, redaction and unavailable metrics. Actual test commands, final counts,
commit references and the PR link belong in the task handoff. No sandbox run or
real-model evaluation is claimed by this implementation walkthrough.
