# CounterSign walkthrough: what we built, step by step

Plain-English explanations of each piece of work, written as it finishes. Each page answers: what does this prove, what do *you* do, what does the script do, what gets saved, how is the verdict decided, and how do you run it.

Claude keeps this index. Codex and the helper agents write their own pages (`c05-*`, `c07-*`) and Claude links them here.

## Start here

| Page | What it explains |
| --- | --- |
| [00-build-review.md](00-build-review.md) | The big picture: everything built up to Oct 23, with diagrams and open decisions |
| [01-today-plan.md](01-today-plan.md) | Today's plan (Oct 23): who does what, and in which order |

## Finished today

| # | Page | Who | Status |
| --- | --- | --- | --- |
| 1 | [02-shared-timeline.md](02-shared-timeline.md): the shared "what happened when" list every check writes to | Claude | Done (`f094649`) |
| 2 | [03-evidence-sent-once.md](03-evidence-sent-once.md): dispute evidence is never sent twice | Claude | Done (`6983de3`) |
| 3 | [c05-d7-negative-values.md](c05-d7-negative-values.md): D7 triggers PayPal's 14 fake-error test values and records the real answers | Claude (helper agent) | Done (`5c02f98`) |
| 4 | [c07-d11-webhooks.md](c07-d11-webhooks.md): D11 checks every webhook's signature with PayPal and measures late, duplicate, out-of-order and missing deliveries | Claude (helper agent) | Done (`16bcaaf`) |
| 5 | [c07-d12-balances.md](c07-d12-balances.md): D12 measures how long PayPal's balance takes to show each payment, hold and refund (the "up to 3 hours" claim) | Claude (helper agent + lead fix) | Done (`48bd7aa`) |
| 6 | [c07-d13-toolkit.md](c07-d13-toolkit.md): D13 proves the AI's PayPal toolkit gets only 5 read-only tools and no tool that can move money | Claude (helper agent) | Done (`15fd617`) |

## In progress

| Check | Page (when finished) | Who |
| --- | --- | --- |
| D2 money in | `c05-d2-money-in.md` | Codex |
| D3 payout happy path | `c05-d3-*.md` | Codex |
| D4 unclaimed payouts | `c05-d4-*.md` | Codex |
| D5 cancel | `c05-d5-*.md` | Codex |
| D6 idempotency (no double payments) | `c05-d6-*.md` | Codex |

## Words used everywhere

- **Check (D1 to D13):** one experiment against the PayPal sandbox, run from the terminal. It records every request and response and ends with a verdict.
- **Verdict:**
  - **CONFIRMED:** PayPal behaves as our design assumed.
  - **DIFFERENT:** it doesn't, so the design must change.
  - **BLOCKED:** we couldn't test it. We write that down and move on.
- **Sandbox:** PayPal's test environment. Fake money, real API.
- **Staged:** something we deliberately do to create a test situation, like the test buyer opening a dispute.
- **UNKNOWN:** we sent a request but never got the answer (for example, the network dropped). We then *look* (GET) to find out what happened. We never just send it again.
