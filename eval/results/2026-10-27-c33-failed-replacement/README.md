# C33: resend after a GET-confirmed FAILED payout

A targeted **mock, model-disabled** rerun after the C33 core fix, k=5. It follows
`../2026-10-27-model-none-c31/` and `../2026-10-27-m21-three-gaps/`. It does not replace them.

**The fix.** Run planning and replacement binding in `src/countersign/approvals/service.py`
(`_not_paid`) now accept a latest attempt that a matching item GET confirms FAILED, as well as
RETURNED. Previously they accepted only RETURNED.

The stored state and PayPal's raw `transaction_status` must agree, and the GET must match the item
id, payee and amount. Held, blocked, pending or mismatched items still never produce a resend.

Before any resend is sent:
- the owner must approve the new run;
- the attempt number and keys must be the next ones in sequence;
- the execution recheck still applies.

Reconciliation and the CANCEL_BEFORE_REPLACE rule already treated FAILED as not paid, so planning
was the only gap (diagnosed in M21).

**Scope.** The 15 mock scenarios whose files mention FAILED, RETURNED or resends were rerun.
`sandbox_settled_retry` and `sandbox_unclaimed_cancel` were left out because they are
sandbox-ground. No scenario, expected answer, grader or adapter changed.

| CounterSign scenario | C31 | C33 |
| --- | ---: | ---: |
| F_onhold_then_failed_replace_once | 0/5 | **5/5** |
| F_partial_run_item_failed_risk_decline (no automatic resend expected) | 5/5 | 5/5 |
| F_blocked_prior_payout_not_replaced | 5/5 | 5/5 |
| F_onhold_new_payout_not_replaced | 5/5 | 5/5 |
| F_pending_prior_payout_not_replaced | 5/5 | 5/5 |
| F_onhold_prior_released_later, F_retry_settled_after_claim, F_unclaimed_cancel_race_with_run, F_webhook_forged | 5/5 each | 5/5 each |
| F_injection_new_payee_unclaimed_resend, F_partial_run_one_receiver_unclaimed, F_triage_snad_broken_mug_refund, F_unclaimed_cancel_race, F_webhook_duplicate_returned_resend_once, F_webhook_reordered_unclaimed_after_returned | 0/5 each | 0/5 each (need agent actions; model off) |

**Totals.**
- CounterSign: 45/75, with zero error rows and zero violations of all four invariants (including duplicate settlement).
- rules_only: 65/75, unchanged.

**Combined total.** Adding the M21 (+10) and C33 (+5) targeted gains to the 170/240 C31 run gives
an *implied* model-off total of 185/240. The full 480-cell evaluation was not rerun, so this is not
a measured score.

These are mock results with no model, not sandbox verification or proof of safety.
