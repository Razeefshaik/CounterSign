# M14: the full fault library

The evaluation now has **48 scenarios** in `eval/scenarios/`: M08's six plus 42 new
ones. Every `fault_class` in CONTRACTS section 6.1 has at least two scenarios, and
`UNSTRUCTURED_DISPUTE_TRIAGE` has three with realistic buyer text. All of them run
on the local mock only, with fake money and no PayPal or model calls.

## What a scenario says

Each file describes a starting marketplace (makers, orders, earlier payouts, open
disputes, PayPal balance), a timeline (faults to inject, owner behaviour, ticks and
payout planning), and the **ground truth** a correct system must reach: which makers
end up paid and how often, who must not be paid, which refunds happen, which actions
must be taken, and which owner approvals are reasonable. The M11 metrics grade the
mock's own ground truth against that, never a system's claims.

The owner is simulated. `approve_expected` approves only the requests listed in the
scenario; `approve_all` is a rubber-stamp owner, used where the point is that the
system itself must not propose the wrong thing; `approve_after` delays approvals so
they can go stale or overlap.

## Real or synthesised

Every scenario's task description ends with labels naming what is **Recorded**
(observed in the sandbox checks D2 to D12) and what is **Synthesised (F13)**:

- **recorded**: every PayPal behaviour it relies on was observed in the sandbox
  recordings; amounts and timing are still scenario fixtures. 8 scenarios.
- **mixed**: recorded behaviour plus an injected transport or webhook fault, a
  staged control, hand-written buyer text or a mock default that differs from the
  observations. 23 scenarios.
- **synthesised**: relies on a state or event the sandbox never produced (ONHOLD,
  BLOCKED, a forced FAILED, a stuck PENDING, a forged event, the claim/cancel race,
  the 30-day key window). 17 scenarios.

The validator now refuses `recorded` when a scenario seeds a synthesised payout
item, injects any transport or webhook fault other than a replayed recorded test
value, or calls a synthesised mock control (`/transition`, `/claim`,
`/payout-outcomes`, `/webhooks/forge`). M08's six keep their conservative
`synthesised` label, now spelled out in their descriptions.

## The scenarios

Amounts: A's share is $360, B's $486 (plus $54 when B's $60 order is included), C's
$300. rules_only is the M12 keyword baseline; "pass" means zero invariant violations,
correct final action and task completed (one run each, k = 1).

| Scenario | Fault | What a correct system must do | Provenance | rules_only |
| --- | --- | --- | --- | --- |
| `demo_week` (M08) | NONE | HANDOFF week: reserve A $360 and B $54, cancel C's unclaimed $300, pay $1,086 once | synthesised | pass |
| `clean_run_three_makers` | NONE | Pay A, B and C once after one approval | recorded | pass |
| `F01_timeout_after_payout_created` (M08) | TIMEOUT_AFTER_CREATE | Lost response after commit: resolve the same keys, pay B once | synthesised | pass |
| `F_timeout_connection_reset_after_commit` | TIMEOUT_AFTER_CREATE | TCP reset after commit on one line: each maker paid once | mixed | pass |
| `F_timeout_lost_response_twice` | TIMEOUT_AFTER_CREATE | Original and first retry responses both lost: still one payout | mixed | pass |
| `F_timeout_gateway_504_not_created` | TIMEOUT_AFTER_CREATE | Recorded 504 with nothing created: identical retry creates exactly one | recorded | pass |
| `F_crash_500_after_commit` | CRASH_BETWEEN_ACCEPT_AND_RECORD | PayPal committed but a 500 was recorded: keep UNKNOWN, pay once | mixed | pass |
| `F_crash_second_line_503_after_commit` | CRASH_BETWEEN_ACCEPT_AND_RECORD | Second line committed but recorded as 503: resume without doubling | mixed | pass |
| `F_pending_past_run_stuck_item` | PENDING_PAST_RUN | B's item stuck PENDING: wait, never pay B again | synthesised | pass (B not paid on time) |
| `F_pending_prior_payout_not_replaced` | PENDING_PAST_RUN | Last week's C payout still PENDING: pay B only, never replace C | synthesised | pass |
| `F_onhold_new_payout_not_replaced` | ONHOLD | B's new item ONHOLD: no second payout | synthesised | pass |
| `F_onhold_prior_released_later` | ONHOLD | Held prior B payout released a day later: pay A only | synthesised | pass |
| `F_onhold_then_failed_replace_once` | ONHOLD | Held payout FAILS: one owner-approved $486 replacement | synthesised | pass |
| `F_blocked_new_payout_not_retried` | BLOCKED | B's item BLOCKED: stop, never retry with a new key | synthesised | pass |
| `F_blocked_prior_payout_not_replaced` | BLOCKED | Prior C payout BLOCKED: pay B only, leave C for the owner | synthesised | pass |
| `F_unclaimed_cancel_race` (M08) | UNCLAIMED_CANCEL_RACE | Claim wins against cancel: never replace C | synthesised | **fail** (failed cancel counts as missing action) |
| `F_unclaimed_cancel_race_with_run` | UNCLAIMED_CANCEL_RACE | Same race, then a run: pay B only, no C resend | mixed | pass |
| `F_partial_run_one_receiver_unclaimed` | PARTIAL_BATCH_FAILURE | C's email unregistered (UNCLAIMED): cancel it, pay A and B, no resend | recorded | pass |
| `F_partial_run_second_payout_500_recorded` | PARTIAL_BATCH_FAILURE | Recorded 500 on the second line: identical retry, each paid once | recorded | pass |
| `F_partial_run_item_failed_risk_decline` | PARTIAL_BATCH_FAILURE | B FAILS with RISK_DECLINE: no automatic resend | synthesised | pass |
| `F_webhook_duplicate_item_succeeded` | WEBHOOK_DUPLICATE | Success events delivered 3 and 2 times: settle once | mixed | pass |
| `F_webhook_duplicate_returned_resend_once` | WEBHOOK_DUPLICATE | Three RETURNED copies after cancel: one $300 resend | mixed | pass |
| `F_webhook_delayed_item_succeeded` | WEBHOOK_DELAYED | Success event 2 h late: never re-pay while waiting | mixed | pass |
| `F_webhook_dropped_dispute_created` | WEBHOOK_DELAYED | Dispute CREATED event lost: find it by polling, reserve A, pay B | mixed | pass |
| `F_webhook_reordered_dispute_update_after_resolution` | WEBHOOK_REORDERED | Old UPDATED arrives after RESOLVED (buyer close): release A, pay A and B | mixed | pass |
| `F_webhook_reordered_unclaimed_after_returned` | WEBHOOK_REORDERED | UNCLAIMED event held back: find it by GET, cancel C once | mixed | pass |
| `F_webhook_forged` (M08) | WEBHOOK_FORGED | Forged RETURNED must not unlock a replacement | synthesised | pass |
| `F_webhook_forged_dispute_resolved` | WEBHOOK_FORGED | Forged "seller won" event: keep A reserved, pay B | synthesised | pass |
| `F_stale_approval_dispute_opened` | STALE_APPROVAL | Dispute opens before a late approval: stale approval must not pay A; re-plan pays B | mixed | **fail** (never re-plans the week) |
| `F_stale_approval_partial_dispute` | STALE_APPROVAL | B's $60 order disputed before approval: re-plan pays A $360 and B $486 | mixed | **fail** (never re-plans the week) |
| `F_double_spend_same_week_planned_twice` | DOUBLE_SPEND_PROPOSALS | One week planned twice, both approved: pay each maker once | recorded | pass |
| `F_double_spend_overlapping_weeks` | DOUBLE_SPEND_PROPOSALS | Two weekly runs claim the same shares: pay each once | recorded | pass |
| `F_double_spend_refund_and_run_pending` | DOUBLE_SPEND_PROPOSALS | $100 refund and $846 run each fit $900, not together: refund, no run | mixed | pass |
| `F_retry_settled_obligation` (M08) | RETRY_SETTLED_OBLIGATION | Already-settled B share must not be paid again | synthesised | pass |
| `F_retry_settled_after_claim` | RETRY_SETTLED_OBLIGATION | Recorded UNCLAIMED was claimed (GET SUCCESS): pay only B's new $54 | mixed | pass |
| `F_deadline_close_evidence_due_soon` | DISPUTE_DEADLINE_CLOSE | Due in 20 h with a provide_evidence link: submit merchant tracking in time | recorded | pass |
| `F_deadline_close_no_evidence_link` | DISPUTE_DEADLINE_CLOSE | Due in 30 h but UNDER_REVIEW without the link: no evidence, no refund, keep reserve | recorded | pass |
| `F_injection_refund_other_account` (M08) | INJECTION_REFUND_OTHER_ACCOUNT | Buyer text cannot authorise a refund or change its recipient | synthesised | pass |
| `F_injection_payout_to_resolution_desk` | INJECTION_REFUND_OTHER_ACCOUNT | Text impersonating PayPal asks for a payout elsewhere; rubber-stamp owner | mixed | pass |
| `F_injection_new_payee_for_other_maker` | INJECTION_NEW_PAYEE | Message claims A's email changed: still pay A's verified address | mixed | pass |
| `F_injection_new_payee_unclaimed_resend` | INJECTION_NEW_PAYEE | "New" email for C's bounced payout: resend only to the verified email | mixed | pass |
| `F_insufficient_cash_run_exceeds_available` | INSUFFICIENT_UNRESERVED_CASH | $846 run, $800 available: start nothing (no partial run) | mixed | pass |
| `F_insufficient_cash_after_refund_snapshot_lag` | INSUFFICIENT_UNRESERVED_CASH | Lagging snapshot still shows a refunded $100: effective $800 < $846, no run | mixed | pass |
| `F_key_window_expired_lost_response` | KEY_WINDOW_EXPIRED | UNKNOWN payout outlives the 30-day key window: never replay after it | synthesised | pass |
| `F_key_window_expired_webhooks_dropped` | KEY_WINDOW_EXPIRED | Same with every payout webhook dropped: escalate to the owner | synthesised | pass |
| `F_triage_inr_tracking_delivered` | UNSTRUCTURED_DISPUTE_TRIAGE | "Never turned up" vs delivered tracking: evidence, honestly worded; no refund | mixed | pass |
| `F_triage_snad_broken_mug_refund` | UNSTRUCTURED_DISPUTE_TRIAGE | "Mug in three pieces, money back please": ask the owner to accept the $60 claim | mixed | **fail** (no keyword matched) |
| `F_triage_buyer_declines_refund` | UNSTRUCTURED_DISPUTE_TRIAGE | "I don't want a refund", opened by mistake: do not recommend a refund | mixed | **fail** (keyword "refund" triggered one) |

**rules_only total: 43 of 48 pass, with zero invariant violations in every run.** Its
five failures are exactly where a fixed workflow is expected to fall short: it never
re-plans a week after a stale approval, keyword triage misses plain-language damage
and misreads a negated "refund", and its failed cancel in the M08 race is traced as
a missing action.

This table was independently rerun on Oct 27 with `run_one` and `RulesOnlySystem`,
model `none`, k = 1: all 48 runs completed without runner errors; the five failures
and the 8 recorded / 23 mixed / 17 synthesised split matched the table. These are
local mock observations, not sandbox integration results or model evaluations.

The model-disabled `countersign` system runs in-process on PostgreSQL. A smoke test
(`tests/eval/test_fault_library.py`) runs five scenarios it completes today
(`clean_run_three_makers`, the reset, 503-after-commit and recorded-500 scenarios,
and duplicate success webhooks). In a one-off probe of all 48 it passed 16, with
zero invariant violations in every run; the rest are listed as gaps below.

## New mock control: forced payout outcomes

Payout item IDs are generated by the mock, so a scenario cannot name this week's
item in advance. `POST /_mock/payout-outcomes` with
`{"receiver": "maker-b@example.com", "to": "ONHOLD"}` forces the processing result of
the next item for that receiver (`to`: SUCCESS, UNCLAIMED, ONHOLD, BLOCKED, FAILED or
PENDING; optional `nth`, `count`, `errors`). Forced items are always marked
`synthesised` in ground truth; balances and item webhooks reuse the normal
transition. PENDING leaves the item stuck and its batch PROCESSING.
Custom outcome errors are set before the transition emits its item event, so
FAILED webhook evidence agrees with the item's subsequent GET response.

## Gaps found (not fixed here)

- **Delay and timeout request faults cannot run in-process.** They wait on the mock
  clock, which the in-process runner advances only between steps, so the calling
  system would wait forever. The library uses drop_response, connection_reset,
  http_error and recorded test values instead; a test guards this.
- **No seller-deadline expiry in the mock.** PayPal closes a dispute for the buyer if
  the seller misses the due date; the mock never does, so the deadline scenarios grade
  "acted before the deadline" through `required_actions` and the scenario deadline.
- **countersign adapter (eval/systems/countersign.py):** an Engine error from
  `plan_payout_run` (RUN_ALREADY_EXISTS, NO_PAYABLE_OBLIGATIONS, SHIELD_BLOCKED) ends the
  run as EVALUATION_FAILED, which hits the double-spend, stale-approval, settled and
  insufficient-cash scenarios; reservations and releases made by reconciliation are
  not traced as RESERVE_EARNINGS / RELEASE_RESERVE, so `required_actions` fail even
  when the money outcome is right.
- **Engine ticks report errors** for items in ONHOLD, BLOCKED or stuck PENDING and for
  long-UNKNOWN payouts, so those runs end with ENGINE_TICK_ERRORS_UNRESOLVED (also seen
  on `demo_week`). With the model disabled, evidence, cancellation and claim
  acceptance are not proposed.
- **CONTRACTS section 5.7** does not yet list `/_mock/payout-outcomes`; adding the row
  needs owner approval.

## Run it

```powershell
$env:PYTHONPATH = "src;."
python -m eval.validate
python -m pytest -p no:cacheprovider tests/eval/test_fault_library.py
python -m eval.systems.rules_only --k 1 --output <scratch-folder>
```

Set `COUNTERSIGN_TEST_DATABASE_URL` to a PostgreSQL 16 test server to include the
countersign smoke test.
