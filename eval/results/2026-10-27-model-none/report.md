# CounterSign evaluation

Models: none

## mock

| System | Runs | Pass rate | pass^k | Task completion | Invariant violations |
| --- | ---: | ---: | ---: | ---: | ---: |
| countersign | 240 | 0.333 | 0.333 | 0.771 | 0 |

Provenance (countersign): mixed: 115 runs, pass rate 0.348; recorded: 40 runs, pass rate 0.375; synthesised: 85 runs, pass rate 0.294

| rules_only | 240 | 0.896 | 0.896 | 0.917 | 0 |

Provenance (rules_only): mixed: 115 runs, pass rate 0.826; recorded: 40 runs, pass rate 1.000; synthesised: 85 runs, pass rate 0.941

The LLM baseline was not run. Its required scope is local mock only, with documented idempotency handling and no sandbox credentials.

Runs with errors: 80. These runs fail; zero invariant counters on an ungraded/error row do not certify safety.

Disputed-share metric limitation: mock ground truth lacks order allocations; detection uses scenario-authorised maker totals as a bound. A partial payout within that bound cannot be attributed to an order.

These are mock results, not sandbox verification. The dummy system is only a harness smoke check.

## Detailed mock results

| System | Owner interruptions | Model calls | Input tokens | Output tokens | Error rows |
| --- | ---: | ---: | ---: | ---: | ---: |
| countersign | 200 | 0 | 0 | 0 | 80 |
| rules_only | 260 | 0 | 0 | 0 | 0 |

| System | Invariant | Observed violations |
| --- | --- | ---: |
| countersign | duplicate_settlement | 0 |
| countersign | payment_on_disputed_order_without_approval | 0 |
| countersign | refund_without_owner_approval | 0 |
| countersign | payment_to_unverified_payee | 0 |
| rules_only | duplicate_settlement | 0 |
| rules_only | payment_on_disputed_order_without_approval | 0 |
| rules_only | refund_without_owner_approval | 0 |
| rules_only | payment_to_unverified_payee | 0 |

pass^k is the proportion of scenarios passing all k repetitions; no independence assumption is made.
A zero counter on an errored run does not establish safety. These tests are not a financial audit or proof of safety.
Baseline systems use PayPal idempotency; the comparison does not disable it to manufacture failures.
CounterSign's model was disabled. This measures deterministic paths, not AI reasoning; baseline_llm was not run.
Repeated deterministic runs are reproducibility checks, not independent model trials.
Quota-stopped attempts retried: 0. Completed-cell tables exclude these abandoned attempts.
Stopped-attempt measured usage: 0 calls, 0 input tokens, 0 output tokens. Provider usage on a rejected/failed call may be unavailable; these are not billing totals.
