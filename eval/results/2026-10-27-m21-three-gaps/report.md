# CounterSign evaluation

Models: none

## mock

| System | Runs | Pass rate | pass^k | Task completion | Invariant violations |
| --- | ---: | ---: | ---: | ---: | ---: |
| countersign | 15 | 0.667 | 0.667 | 0.667 | 0 |

Provenance (countersign): synthesised: 15 runs, pass rate 0.667

The LLM baseline was not run. Its required scope is local mock only, with documented idempotency handling and no sandbox credentials.

Runs with errors: 0. These runs fail; zero invariant counters on an ungraded/error row do not certify safety.

Disputed-share metric limitation: mock ground truth lacks order allocations; detection uses scenario-authorised maker totals as a bound. A partial payout within that bound cannot be attributed to an order.

These are mock results, not sandbox verification. The dummy system is only a harness smoke check.
