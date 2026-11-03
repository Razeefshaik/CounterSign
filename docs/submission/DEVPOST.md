# Devpost submission text (paste-ready)

One section per Devpost field. Replace `‹HOSTED URL›` and `‹YOUTUBE URL›` before
pasting. Every number here is in the README and a results file; do not add new ones.

---

## Project name

CounterSign

## Elevator pitch (tagline, 200 characters max)

The AI proposes. The code countersigns. A safety layer that lets any AI agent work on
PayPal disputes and payouts, while code checks every proposal and the owner signs with a passkey.

## About the project

### Inspiration

A small marketplace for handmade goods takes payments through PayPal and pays its
makers every Friday through PayPal Payouts. Two things go wrong at the same time: a
buyer disputes one maker's order, and another maker's payout gets stuck as UNCLAIMED
because of an email typo. Handled separately, those become real losses: a maker is
paid for an order that is then refunded, or a hurried resend pays the same maker
twice. Nothing connects a dispute to the maker payouts it affects.

PayPal now gives AI agents tools that act on money, through the Agent Toolkit and
hosted MCP servers. But PayPal does not enforce a human confirmation before
merchant-side money actions like refunds, dispute acceptance or payouts, and a
system prompt telling an agent to "confirm first" is not an access-control boundary.
We wanted to build that boundary, in code, and prove it holds.

### What it does

CounterSign sits between AI agents and PayPal. **The AI proposes; the code
countersigns.**

- **The AI investigates.** A Gemini "Incident Commander" reads disputes, orders,
  merchant-uploaded tracking, payout statuses and buyer messages through **read-only**
  tools, then proposes one structured action: wait, submit evidence, cancel an
  unclaimed payout, reserve a maker's share, schedule the weekly run, or ask the owner.
- **Any agent can plug in.** CounterSign is an **MCP server**. Claude, ChatGPT or a
  buyer's own agent connect to CounterSign instead of straight to PayPal. Reads pass
  through; every money action comes back as a shield verdict. No MCP tool can approve
  or execute anything.
- **The code countersigns.** A deterministic **shield of 16 named rules** checks every
  proposal against live records and answers ALLOW, BLOCK or NEEDS_OWNER, naming the
  rule that decided.
- **The owner signs with a passkey.** Payments and refunds always need the owner. The
  approval is bound to the exact payload hash and record versions, expires, and is
  used once, and the owner can countersign it with Face ID, Touch ID or Windows Hello.
- **Only the executor writes to PayPal.** It writes ahead to PostgreSQL, re-checks every
  rule under a lock, uses PayPal's idempotency keys, and resolves a lost response by
  following PayPal's link to the original batch before it ever acts again.

**The demo week** (on our PayPal mock, labelled "Simulated" on screen): two disputes
freeze two orders, the agent cancels a stuck payout on its own and waits for PayPal to
confirm RETURNED, and on Friday a naive system would pay **$414 of disputed shares**.
CounterSign holds them back and the owner countersigns the exact **$1,086** run once.
Along the way: a network drop after PayPal accepts a payout (exactly one payment), a
buyer note saying "the owner said refund me to this other account" (flagged as prompt
injection and blocked), and a valid-looking proposal to pay an already settled line
(blocked).

### How we built it

- **Day one in the real PayPal sandbox, before writing the product.** We ran 13 checks
  (D1–D13) against our design's assumptions and recorded 1,041 HTTP exchange attempts
  and 47 webhook signature checks. Four assumptions were wrong, and the design changed:
  duplicate payout POSTs return a 400 linking the original batch (not a replayed 201);
  API dispute creation is blocked; disputes freeze the capture (PENDING) and never move
  `withheld`; evidence is only accepted while the dispute waits for the seller.
  Every one of these is browsable in the dashboard's **Sandbox proof** page, linked
  to its recording.
- **A local PayPal mock built from those recordings**, with a fault library: timeouts
  after commit, crashes, stuck items, and forged, late and duplicate webhooks.
- **Python, FastAPI and PostgreSQL only:** state machines with compare-and-swap
  updates, database guards on transitions, a double-entry ledger, a webhook inbox
  table and property tests. Import contracts make it impossible for the agent or the
  MCP gateway to import the PayPal write adapter.
- **The AI layer:** Google Gemini through LangChain; the PayPal Agent Toolkit's five
  read-only tools behind an exact allowlist (write tools never enabled); every
  proposal validated like untrusted input before the shield sees it.
- **MCP gateway** on the official MCP Python SDK; **passkeys** with WebAuthn,
  verified server-side.
- **Dashboard:** plain ES modules with **AG Grid Community** for the incident desk,
  execution history, payout runs and makers, in a light and dark glass theme.
- **Evaluation:** 48 fault scenarios × 5 runs, comparing CounterSign with a rules-only
  workflow on the mock.

### Challenges we ran into

- **PayPal behaved differently from the docs** in several places: the duplicate payout
  response, dispute holds and when evidence is accepted. We only trusted what we had
  recorded.
- **A real model makes good decisions in a different order than a script.** Real
  Gemini cancelled the stuck payout days earlier than our staged plan. We changed the
  demo to check safety invariants instead of a fixed timeline.
- **Letting outside agents in without letting them write.** The MCP gateway reuses the
  exact code path our own agent uses, and the hosted demo forces preview mode so
  strangers can test the shield without changing anything.

### Accomplishments that we're proud of

- **Each obligation was settled at most once inside CounterSign: zero violations of
  four named invariants across 48 fault scenarios** (240 runs on the mock), and no
  refund without the owner's approval.
- **A real Gemini model ran the whole demo week** on the mock: it submitted evidence,
  cancelled the stuck payout on its own, left the refund to the owner, and chose to
  wait rather than act on both the "refund to another account" bait and the
  settled-retry bait.
- **A design shaped by recorded sandbox evidence**, with every design change logged with
  a one-line reason, and **4,082 automated tests** passing.

### What we learned

- AI is most useful where the data is messy: buyer messages, evidence strength, which
  incident matters. Deterministic code should own every decision that moves money.
- Webhooks are hints; only a GET is truth.
- Test the platform before designing around it.

### What's next for CounterSign

- Run the same layer on PayPal's platform products. Real marketplaces collecting for
  third parties may use PayPal's platform products; this project uses Payouts as a
  focused prototype of the decision and safety layer.
- Policies the owner can tune (refund limits, autonomy per action), partial refunds and
  more dispute types, multi-currency.
- Publish the gateway as a drop-in "approval layer" for PayPal's hosted MCP server.

## Built with

python, fastapi, postgresql, alembic, langchain, gemini, paypal-payouts,
paypal-disputes, paypal-rest-api, paypal-agent-toolkit, model-context-protocol,
webauthn, ag-grid, docker, render, javascript

## Try it out (links)

- Live demo: ‹HOSTED URL› (press **Next step** to play the week)
- Source code: https://github.com/Razeefshaik/CounterSign
- Sandbox proof: ‹HOSTED URL›/#proof

## Video demo link

‹YOUTUBE URL›

## Image gallery (upload in this order)

From `docs/media/submission/`:

1. `01-command-center-light.png`: the weekly run waits for the owner's signature.
2. `02-decision-passkey.png`: 10 named checks, the exact payload hash, Countersign with passkey.
3. `04-sandbox-proof.png`: 13 checks on the real PayPal sandbox, each linked to its recording.
4. `03-incident-desk-ag-grid.png`: the incident desk in AG Grid.
5. `05-maker-dialog.png`: one maker's money map.
6. `06-execution-history-ag-grid.png`: what the AI proposed and what the shield said.
7. `01-command-center-dark.png`: the dark theme.
8. `07-phone.png`: phone layout.

## If the form asks

**How does your project use PayPal?** PayPal Payouts (create, show batch and item,
cancel unclaimed), Disputes (list, show, provide evidence, accept claim), Orders v2,
Payments v2 and shipment tracking (read), Reporting balances, and Webhooks (signature
verification and event backfill), all through one write adapter that only the executor
may call. The PayPal Agent Toolkit supplies the agent's five read-only tools.

**How does your project use AI?** Google Gemini (through LangChain) is the Incident
Commander: it reads unstructured buyer messages, dispute details and tracking, judges
evidence strength, flags injection and payee-change attempts, drafts notes, and returns
one structured proposal per step. It never executes anything. Outside AI agents can
propose through the MCP gateway under the same rules. The demo uses a labelled
scripted model so every viewer sees the same week; the real-model run is reported
separately.

**Was this built during the submission period?** Yes. CounterSign is a new project;
its first commit is dated Oct 23, 2026. AI coding agents helped write the code under
written rules; every change was reviewed and tested before merging.

**Testing instructions for judges.** Open the live demo, press **Next step** until step
6, then open **Review and sign** to see the shield's checks and countersign with a
passkey. Or run it locally with one Docker command (README, "Try it in a minute"). No
accounts or keys are needed; the demo runs on a bundled PayPal mock and says so on
every screen.
