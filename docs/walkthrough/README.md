# CounterSign walkthrough: what we built, step by step

Plain-English explanations of each piece of work, written as it finishes. Each page answers: what does this prove, what do *you* do, what does the script do, what gets saved, how is the verdict decided, and how do you run it.

Claude keeps this index. Codex and the helper agents write their own pages (`c05-*`, `c07-*`) and Claude links them here.

## Start here

| Page | What it explains |
| --- | --- |
| [00-build-review.md](00-build-review.md) | The big picture: everything built up to Oct 23, with diagrams and open decisions |
| [01-today-plan.md](01-today-plan.md) | The Oct 23 plan: who did what, and why in that order |
| [04-run-sheet.md](04-run-sheet.md) | **Oct 24 and 25: exactly what to run, in which terminal, and what you click** |

## Finished on Oct 23 (all 13 checks are now built and tested)

| # | Page | What it is | Who | Commit |
| --- | --- | --- | --- | --- |
| 1 | [02-shared-timeline.md](02-shared-timeline.md) | One shared "what happened when" list every check writes to | Claude | `6b83518` |
| 2 | [03-evidence-sent-once.md](03-evidence-sent-once.md) | Dispute evidence is never sent twice | Claude | `e524df1` |
| 3 | [c05-d2-money-in.md](c05-d2-money-in.md) | **D2**: the test buyer pays $400, $60 and $25; tracking is added | Codex | `0ea45f7` |
| 4 | [c05-d3-payout.md](c05-d3-payout.md) | **D3**: a $1 payout to Maker A reaches SUCCESS | Codex | `df53509` |
| 5 | [c05-d4-unclaimed.md](c05-d4-unclaimed.md) | **D4**: payouts to never-registered emails become UNCLAIMED; does the balance drop? | Codex | `4dcb653` |
| 6 | [c05-d5-cancel.md](c05-d5-cancel.md) | **D5**: cancelling an unclaimed payout returns the money | Codex | `1817b59` |
| 7 | [c05-d6-idempotency.md](c05-d6-idempotency.md) | **D6**: repeated or lost requests never pay twice | Codex | `4aa21ac` |
| 8 | [c05-d7-negative-values.md](c05-d7-negative-values.md) | **D7**: PayPal's 14 fake-error test codes | Claude (helper) | `93348e7` |
| 9 | [c07-d11-webhooks.md](c07-d11-webhooks.md) | **D11**: webhook signatures, delays, duplicates, missing messages | Claude (helper) | `6794e1c` |
| 10 | [c07-d12-balances.md](c07-d12-balances.md) | **D12**: how long the balance takes to show each event ("up to 3 hours") | Claude (helper + lead fix) | `72b0503` |
| 11 | [c07-d13-toolkit.md](c07-d13-toolkit.md) | **D13**: the AI's toolkit gets only 5 read-only tools, never a money tool | Claude (helper) | `de44cd8` |

D1 (scopes, done and CONFIRMED on Oct 23) and D8 to D10 (disputes) are explained in [00-build-review.md](00-build-review.md), sections 5 and 9.

All commit hashes above are as they appear on `main`.

## Phase 2: the vertical slice (started Oct 24, milestone Nov 5)

The first working version of the product itself, built step by step. Hashes below are on branch `razeef/fsm-core` until it is merged into `main`.

| # | Page | What it is | Who | Commit |
| --- | --- | --- | --- | --- |
| 12 | [c10-money.md](c10-money.md) | **Money**: always whole cents; splits never lose or invent a cent; strict PayPal amount parsing | Claude (helper) | `726339f` |
| 13 | [c10-payout-state-machines.md](c10-payout-state-machines.md) | **Payout rules**: a maker is "paid" only when PayPal says SUCCESS; unclaimed never counts | Claude (helper + lead fix) | `f2a7d19` |
| 14 | [c10-control-state-machines.md](c10-control-state-machines.md) | **Control rules**: lost writes stay UNKNOWN, only approved proposals execute, unknown dispute outcomes keep the money reserved | Claude (helper) | `ebf9866` |
| 15 | [c11-schema.md](c11-schema.md) | **Database tables**: one live payout attempt per line, ledger journals must balance | Codex | `2e97840` |
| 16 | [c11-dashboard-views.md](c11-dashboard-views.md) | **Dashboard views**: read-only data shaped for the incident desk; the AI's database role can only read these | Codex | `cc1a9f2` |
| 17 | [c12-database-layer.md](c12-database-layer.md) | **Safe updates**: change a row only if nobody changed it first | Codex | `4e5c8d5` |
| 18 | [05-state-guards.md](05-state-guards.md) | **Closing a gap**: version numbers on every row a state machine moves, and a test that keeps database and code in step | Claude | `da027ac` |
| 19 | [c16-ledger.md](c16-ledger.md) | **Bookkeeping and cash**: every money move balanced and booked once; dispute earmarks never reduce free cash; demo Thursday gives exactly $254 free | Claude (+ helper) | `c5a67e5`, `2930804` |
| 20 | [c17-shield.md](c17-shield.md) | **The shield**: 10 safety rules every AI proposal must pass (no double pay, only verified emails, enough free cash, approvals bound to exact records) | Claude (helper) | `e6f2218` |
| 21 | [c13-paypal-adapter.md](c13-paypal-adapter.md) | **Talking to PayPal**: reads anyone may use; writes only from a saved operation, never retried automatically, and any unclear answer counts as "unknown", never "failed" | Codex | `65b47db` |
| 22 | [c18-owner-approvals.md](c18-owner-approvals.md) | **Owner approvals**: the weekly run plan, and an approval that is tied to the exact records it saw and expires after 30 minutes | Codex | `aaa8a98`, `4b4c6a1` |
| 23 | [c14-webhook-inbox.md](c14-webhook-inbox.md) | **Webhook inbox**: every PayPal message saved once with its exact bytes; acted on only after PayPal confirms the signature, and a dispute reserves money only after a GET confirms it | Codex | `022b199` |
| 24 | [c19-executor.md](c19-executor.md) | **The executor**: the only code that sends money; saves before sending, re-checks every rule, uses each approval once, and settles unclear answers only by repeating the identical request | Claude | `de708b1` |
| 25 | [c15-reconciliation.md](c15-reconciliation.md) | **Reconciliation poller**: regularly asks PayPal for the truth about every payout, dispute and balance, corrects our records, and opens an incident when something moves unexpectedly | Codex | `37747c9`, `13c93d8` |
| 26 | [c20-engine-facade.md](c20-engine-facade.md) | **Engine facade**: the one stable door the evaluation, the demo bar and Aman's code use; it can wipe only a database named `_test` or `_eval`, and the worker now runs the poller and the unknown-payout sweep | Codex | `3872557`, `18613fb` |

## After the slice

| Page | What it explains |
| --- | --- |
| [c24-rules-incidents-simulator.md](c24-rules-incidents-simulator.md) | Dispute rules, reconciliation-only incident closure and read-only payout cash comparisons |

## Words used everywhere

- **Check (D1 to D13):** one experiment against the PayPal sandbox, run from the terminal. It records every request and response and ends with a verdict.
- **Verdict:**
  - **CONFIRMED:** PayPal behaves as our design assumed.
  - **DIFFERENT:** it doesn't, so the design must change.
  - **BLOCKED:** we couldn't test it. We write that down and move on.
- **Sandbox:** PayPal's test environment. Fake money, real API.
- **Staged:** something we deliberately do to create a test situation, like the test buyer opening a dispute.
- **UNKNOWN:** we sent a request but never got the answer (for example, the network dropped). We then *look* (GET) to find out what happened, or, for payouts, repeat the *identical* request with the same keys, which PayPal treats as the same request. We never send a new one.
