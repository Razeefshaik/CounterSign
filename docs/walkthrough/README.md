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
| 1 | [02-shared-timeline.md](02-shared-timeline.md) | One shared "what happened when" list every check writes to | Claude | `f094649` |
| 2 | [03-evidence-sent-once.md](03-evidence-sent-once.md) | Dispute evidence is never sent twice | Claude | `6983de3` |
| 3 | [c05-d2-money-in.md](c05-d2-money-in.md) | **D2**: the test buyer pays $400, $60 and $25; tracking is added | Codex | `5273e05` |
| 4 | [c05-d3-payout.md](c05-d3-payout.md) | **D3**: a $1 payout to Maker A reaches SUCCESS | Codex | `2ccbb51` |
| 5 | [c05-d4-unclaimed.md](c05-d4-unclaimed.md) | **D4**: payouts to never-registered emails become UNCLAIMED; does the balance drop? | Codex | `63d6077` |
| 6 | [c05-d5-cancel.md](c05-d5-cancel.md) | **D5**: cancelling an unclaimed payout returns the money | Codex | `a2059aa` |
| 7 | [c05-d6-idempotency.md](c05-d6-idempotency.md) | **D6**: repeated or lost requests never pay twice | Codex | `6fe9b10` |
| 8 | [c05-d7-negative-values.md](c05-d7-negative-values.md) | **D7**: PayPal's 14 fake-error test codes | Claude (helper) | `5c02f98` |
| 9 | [c07-d11-webhooks.md](c07-d11-webhooks.md) | **D11**: webhook signatures, delays, duplicates, missing messages | Claude (helper) | `16bcaaf` |
| 10 | [c07-d12-balances.md](c07-d12-balances.md) | **D12**: how long the balance takes to show each event ("up to 3 hours") | Claude (helper + lead fix) | `48bd7aa` |
| 11 | [c07-d13-toolkit.md](c07-d13-toolkit.md) | **D13**: the AI's toolkit gets only 5 read-only tools, never a money tool | Claude (helper) | `15fd617` |

D1 (scopes, done and CONFIRMED on Oct 23) and D8 to D10 (disputes) are explained in [00-build-review.md](00-build-review.md), sections 5 and 9.

Commit hashes are from the branch where each piece was written; the other branch has the same change under a cherry-picked hash.

## Words used everywhere

- **Check (D1 to D13):** one experiment against the PayPal sandbox, run from the terminal. It records every request and response and ends with a verdict.
- **Verdict:**
  - **CONFIRMED:** PayPal behaves as our design assumed.
  - **DIFFERENT:** it doesn't, so the design must change.
  - **BLOCKED:** we couldn't test it. We write that down and move on.
- **Sandbox:** PayPal's test environment. Fake money, real API.
- **Staged:** something we deliberately do to create a test situation, like the test buyer opening a dispute.
- **UNKNOWN:** we sent a request but never got the answer (for example, the network dropped). We then *look* (GET) to find out what happened, or, for payouts, repeat the *identical* request with the same keys, which PayPal treats as the same request. We never send a new one.
