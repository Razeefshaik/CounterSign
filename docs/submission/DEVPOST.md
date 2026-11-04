# Devpost submission text (paste-ready)

One section per Devpost field. Replace `‹HOSTED URL›` and `‹YOUTUBE URL›` before
pasting. Evidence links distinguish the simulated week, sandbox checks and real-model run.

---

## Project name

CounterSign

## Elevator pitch (tagline, 200 characters max)

Let AI investigate PayPal disputes and payouts. CounterSign checks each proposal in code, holds disputed maker shares, and lets the owner countersign the exact payment details with a passkey.

## About the project

### Inspiration

Marketplaces and platforms that pay many sellers through PayPal have to connect
buyer disputes to maker payouts. Enough cash in the account does not make a
disputed share payable. A stuck payout also needs investigation before a resend.

AI can help read the complaint, tracking and payout status. Its suggestion still
needs an application permission check. We built a boundary where the agent proposes,
code checks the records, and the owner approves the exact payment or buyer refund.

### What it does

**CounterSign gives the merchant one workspace for disputes, maker payouts and
the decisions between them.** It is a focused prototype for marketplaces and
platforms that want AI assistance with PayPal payouts.

- **Command center** shows the next owner action, maker balances and the staged week.
  **Decisions** shows the proposal, applicable checks, exact payload hash and
  **passkey countersign**. Plain approval remains available and is labelled separately.
- **Incident desk** filters disputes and stuck payouts. **Payout runs** exposes maker
  lines, attempts and resends. **Cash** shows freshness and the spendable-cash
  calculation. **Money graph** links the disputed order to maker shares and holds;
  **the maker's money map** opens that maker's records in a dialog.
- **Execution history** traces verdicts, approvals and PayPal operations.
  **Evaluation replay** explores historical mock runs with AG Grid, filtered totals
  and CSV export. **Sandbox proof** links observed PayPal behavior to recordings.
- **Try to trick the agent** runs the proposal a fooled agent would make through
  the real shield, then rolls back the preview. It makes no Gemini call.
  **Light and dark themes**, a **phone layout** and reduced-motion support carry
  the same decisions and simulation label across the product.

The Gemini Incident Commander reads sanitized incidents, buyer messages and
read-only tools. It returns one validated structured proposal with action,
parameters, evidence references, rationale and expected effect. A deterministic
shield evaluates the applicable rules. Payments and buyer refunds require a bound
owner approval; the executor rechecks before its adapter writes to PayPal.

An outside MCP agent gets the same boundary. Hosted HTTP is a rollback preview;
local stdio can record a proposal for review. No MCP tool approves or executes it.
The recorded SDK exchange returns `BLOCK`, names the payee and injection rules,
and says `written: false` for a refund-to-another-account attempt.

**The demo opens on unsigned Friday.** It runs on a local PayPal mock and says
**Simulated: mock PayPal**. A naive read-only comparison would pay $1,500, including
$414 of disputed shares. CounterSign holds A's $360 and B's $54, and asks the owner
to approve $1,086 for B and C. A staged lost response is recovered by following the
original-batch link in a duplicate 400 response; the intended payout is paid once.
In the following week, A's staged seller win is reconciled before the owner
approves its $360. Available cash ends at $354; B's separate $60 refund remains
unapproved. The money is simulated, not a claim of customer savings.

### How we built it

- **PayPal sandbox research shaped the design.** Across checks D1–D13 we recorded
  1,041 HTTP exchange attempts, including transport failures. The 47 successful
  webhook signature checks include the simulator event. Four summary checks differed
  from our assumptions: duplicate payout responses, dispute creation and holds,
  evidence availability, and balance freshness. The dashboard links those findings
  to the actual recordings.
- **A local PayPal mock reproduces the recorded behaviors.** Faults include a
  timeout after acceptance, crashes, stuck items and forged or reordered webhooks.
  A fresh resource GET confirms state; an event alone does not settle the record.
- **Python, FastAPI and PostgreSQL 16 hold the execution path.** State machines,
  record versions, write-ahead operations, a double-entry ledger and a PostgreSQL
  webhook inbox keep the checks close to the records. There is no graph database
  or separate message queue.
- **16 named safety boundaries** comprise 14 pure shield rules, execution recheck
  and webhook authenticity. A payout proposal has 10 applicable pre-execution
  checks; unrelated checks are not shown as if they ran. Import-linter contracts
  enforce the agent, pure-shield, write-adapter and gateway package boundaries.
- **Gemini through LangChain investigates and proposes.** The PayPal Agent Toolkit
  exposes five read-only tools behind an exact allowlist and HTTP guard. Model
  output, buyer text and tool results remain untrusted. Only the owner can change
  the trusted payout address.
- **The official MCP Python SDK** carries read and proposal tools. **WebAuthn**
  verifies device signatures on the server while retaining the original approval
  binding and expiry checks.
- **AG Grid Community** powers operational tables and the historical evaluation
  explorer. A **Render Docker Blueprint** packages the app, PostgreSQL and loopback
  mock, with a health check and no PayPal or model keys. Public Render deployment
  and hosted verification remain pending while the URL is a placeholder.

### Challenges we ran into

- **Observed PayPal behavior changed the design.** A duplicate payout POST returned
  a 400 with an original-batch link, rather than a replayed create response. Disputed
  captures became PENDING without an increase in withheld. Evidence required the
  right dispute state and action link. We built against those recordings.
- **The model did not follow the script.** In the recorded Gemini week it chose
  WAIT on both unsafe baits, so those particular shield-block scenes were absent.
  We kept that result instead of inventing agent actions.
- **A shared demo needs a reviewable starting point.** The hosted image restores
  the unsigned Friday state from real scripted steps. It retains passkeys, clears
  challenges and waits for active decisions before an idle restoration.

### Accomplishments that we're proud of

The merchant can inspect a proposal, sign its exact details and follow its outcome
through the execution history and money map. The sandbox proof page and historical
evaluation explorer make the supporting evidence inspectable too.

**Historical fault evaluation: mock, model disabled.** The full published experiment
contains 48 scenarios, five runs per scenario, for each system:

| System | Passing runs | Scenarios passing 5/5 |
| --- | ---: | ---: |
| CounterSign, model disabled | 170/240 | 34/48 |
| Rules-only workflow | 215/240 | 43/48 |

Both systems recorded zero for each of four measured invariant counters: duplicate
settlement, disputed-order payment without approval, refund without owner approval,
and payment to an unverified payee. Rules-only completed more tasks in this model-off
experiment. Agent-only actions were missing when the model was disabled. Targeted
follow-ups do not replace the original full score. Zero measured violations do not
establish universal safety.

**Real Gemini evidence is separate.** The recorded `gemini-3.5-flash-lite` week ran
on the mock and finished `COMPLETE_WITH_GAPS`. It submitted evidence, cancelled
the unclaimed payout, drafted a maker note and left the refund to the owner. It
chose WAIT on the injection and settled-retry baits. This is not a real-model
benchmark score. The public demo instead uses a labelled scripted model, with no
provider calls, so visitors can replay the same safety scenes.

### What we learned

AI helps investigate unstructured complaints and evidence. Deterministic records,
checks and owner authority still have to decide whether a financial write is
permitted. Webhooks need verification and resource confirmation. Recorded platform
behavior is a stronger design input than an untested assumption.

### What's next for CounterSign

- Apply the decision and approval layer to PayPal platform products for real
  marketplaces collecting for third parties. This project uses Payouts as a
  focused prototype; it is not a production platform integration.
- Add owner-tunable policies, partial refunds, more dispute types and multi-currency.
- Extend the gateway as an approval layer for PayPal's hosted MCP workflows.

## Tools used, and how

**PayPal endpoints first.** The runtime inventory has 17 distinct HTTP method/path
pairs; identifiers and query strings do not add endpoints. Sandbox setup and test
controls are excluded. [Counting scope and code links](../../README.md#paypal-calls).

| PayPal product | Runtime endpoints | Product use and sandbox evidence |
| --- | --- | --- |
| Payouts | `POST /v1/payments/payouts`; `GET /v1/payments/payouts/{batch_id}`; `GET /v1/payments/payouts-item/{item_id}`; `POST /v1/payments/payouts-item/{item_id}/cancel` | Weekly maker attempts and uncertain-payment recovery. [D3](../../recordings/sandbox/2026-10-23/D3/), [D5](../../recordings/sandbox/2026-10-24/D5/), [D6](../../recordings/sandbox/2026-10-24/D6/). |
| Disputes | `GET /v1/customer/disputes`; `GET /v1/customer/disputes/{dispute_id}`; `POST /v1/customer/disputes/{dispute_id}/provide-evidence`; `POST /v1/customer/disputes/{dispute_id}/accept-claim` | Link claims to maker holds, submit eligible evidence and leave refunds to owner approval. [D8–D10 findings](../SANDBOX_FINDINGS.md). |
| Orders v2 | `GET /v2/checkout/orders/{order_id}` | Read the order behind a dispute. [D2](../../recordings/sandbox/2026-10-23/D2/). |
| Payments v2 | `GET /v2/payments/captures/{capture_id}` | Confirm the capture state. [D8](../../recordings/sandbox/2026-10-24/D8/). |
| Shipment tracking | `GET /v1/shipping/trackers` | Read merchant-uploaded tracking; do not claim independent carrier verification. [D2](../../recordings/sandbox/2026-10-23/D2/26_get_tracking.json). |
| Reporting | `GET /v1/reporting/balances`; `GET /v1/reporting/transactions` | Cash freshness and toolkit Transaction Search; search lag remains unverified. [D12](../../recordings/sandbox/2026-10-24/D12/). |
| Webhooks | `POST /v1/notifications/verify-webhook-signature`; `GET /v1/notifications/webhooks-events`; `GET /v1/notifications/webhooks-events/{event_id}` | Verify and backfill events, then confirm resource state. [D11](../../recordings/sandbox/2026-10-26/D11/). |
| OAuth | `POST /v1/oauth2/token` | Authenticate the sandbox readers and executor adapter. [D1](../../recordings/sandbox/2026-10-23/D1/). |

- **PayPal Agent Toolkit:** `list_disputes`, `get_dispute`, `get_shipment_tracking`,
  `get_order_details`, `list_transactions`. `accept_dispute_claim` and `create_refund`
  are not enabled; `disputes.create` is not set. CounterSign adds payout-item,
  fixed-query money-graph and simulator reads. [Guard](../../src/countersign/agent/toolkit_child.py).
- **Google Gemini through LangChain:** the bounded Incident Commander. It reads
  sanitized context and returns one structured proposal; it cannot approve or execute.
  [Code](../../src/countersign/agent/), [real-model week](../../recordings/real_model/2026-10-27/README.md).
- **Model Context Protocol, official Python SDK:** six reads and five proposal
  tools; hosted rollback preview and opt-in local recording. [Actual SDK preview](../../design-demos/reference/c48/http.json).
- **AG Grid Community:** operational incident, audit, payout and maker grids; a
  480-run historical explorer with paired presets, filter-aware totals and CSV export.
  [Operational bridge](../../web/grid.mjs), [explorer](../../web/eval-explorer.mjs).
- **Render:** Docker Blueprint, `/api/v1/meta` health check, loopback PostgreSQL
  and mock, and an unsigned Friday checkpoint. Public deployment is pending.
  [Blueprint](../../render.yaml).
- **WebAuthn (py_webauthn):** payload-bound passkey approval, verified server-side.
- **Python, FastAPI, PostgreSQL 16, Alembic, Docker and JavaScript:** the app,
  transactional state, migrations, no-key demo container and ES-module dashboard.
- **AI coding agents:** helped write and review the code under written ownership
  and verification rules.

## Evidence against each judging criterion

The [official criteria](https://paypalaihackathon.devpost.com/rules) have equal
weight; the listed order breaks ties.

- **Technological Implementation:** runtime PayPal reads and guarded writes,
  Gemini's read/propose loop, the MCP boundary and exact owner approval.
  [Endpoint inventory](../../README.md#paypal-calls), [sandbox recordings](../../recordings/sandbox/INDEX.md).
- **Design:** a merchant can investigate, review, sign and track a payment in the
  same light, dark or phone workspace. [Every page](../../README.md#tour-the-product).
- **Potential Impact:** marketplaces paying many makers need to keep disputed
  shares out of apparently affordable runs; the $414 example demonstrates that
  need without claiming real customer savings. [Staged scenario](../walkthrough/c25-demo-week.md).
- **Innovation/Idea:** outside and internal agents cross the same deterministic
  authorization boundary; a passkey can bind the human decision to exact details.
  [Gateway](../../src/countersign/gateway/), [approvals](../../src/countersign/approvals/).
- **Presentation:** Friday decision, Monday replay and following-week release are
  a coherent end-to-end story, backed by separate evidence sources.
  [Friday checkpoint](../../design-demos/reference/c62/README.md),
  [historical results and limits](../../eval/results/2026-10-27-model-none-c31/README.md),
  [real-model gaps](../../recordings/real_model/2026-10-27/README.md).

## Built with

python, fastapi, postgresql, alembic, langchain, gemini, paypal-payouts,
paypal-disputes, paypal-rest-api, paypal-agent-toolkit, model-context-protocol,
webauthn, ag-grid, docker, render, javascript

## Try it out (links)

- Live demo: ‹HOSTED URL› (opens on unsigned Friday; choose **Review and sign**).
- Source code: https://github.com/Razeefshaik/CounterSign
- Sandbox proof: ‹HOSTED URL›/#proof

## Video demo link

‹YOUTUBE URL›

## Image gallery (upload in this order)

From `docs/media/submission/`. These existing images need the Friday-start refresh
before final upload; the passkey image shows controls before signing.

1. `01-command-center-light.png`: the owner action in the light workspace.
2. `02-decision-passkey.png`: applicable payout checks, exact hash and countersign controls.
3. `04-sandbox-proof.png`: recorded sandbox checks and source links.
4. `08-evaluation-explorer-ag-grid.png`: historical model-disabled results and filters.
5. `03-incident-desk-ag-grid.png`: operational incident filtering.
6. `05-maker-dialog.png`: a maker's money map with its simulation label.
7. `06-execution-history-ag-grid.png`: verdict, owner and operation records.
8. `01-command-center-dark.png`: the dark workspace.
9. `07-phone.png`: the phone layout.

## If the form asks

**How does your project use PayPal?** Payouts, Disputes, Orders v2, Payments v2,
shipment tracking, Reporting and Webhooks provide the records the product
reconciles. OAuth authenticates the sandbox adapters. Financial writes are
restricted to the executor adapter after shield checks and required owner approval.
The endpoint list and recordings are above; the Agent Toolkit exposes only its
five allowlisted reads.

**How does your project use AI?** Gemini through LangChain investigates sanitized
incidents and read-only evidence, flags suspicious text and returns one structured
proposal. It never approves or executes. Outside MCP agents get the same boundary.
The public week uses a labelled scripted model. The real Gemini mock week is
reported separately with its WAIT choices and gaps intact.

**Was this built during the submission period?** Yes. CounterSign is a new project;
its first commit is dated Oct 23, 2026. AI coding agents assisted under written
ownership and review rules.

**Testing instructions for judges.** Open the live demo: it starts on Friday with
the weekly run waiting for you. Choose **Review and sign** to inspect the checks
and exact details. Countersign with a passkey, or use plain approval. Then use
**Next step** to continue. **Watch from Monday** restarts the earlier story with
**Start the week** and **Next step**. At the end, **Back to Friday** restores the
unsigned run. A shared demo can change while another visitor uses it; idle return
defaults to 20 minutes and waits for active decisions and passkey prompts.
**Evaluation replay** explores the historical runs; **Sandbox proof** opens their
separate recording evidence. No account or keys are needed. The no-key local Docker
route is in the README's **Try it in a minute**. All demo scenes use the bundled
PayPal mock and keep the simulation label visible.
