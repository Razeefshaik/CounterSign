# Model-disabled evaluation after C31 and reserve observation

Source: `09034ce` (main `bb77df0` with C31, plus the adapter change). Same 48 scenario hashes, the two systems `rules_only` and `countersign`, and k=5, as the two earlier runs. Both earlier folders are kept: `../2026-10-27-model-none/` and `../2026-10-27-model-none-fixed/`.

What changed since the `-fixed` run:
- **C31 (core):** an expired or stale payout-run approval request now refreshes into a new owner request instead of stranding the week. Run requests live 24 hours.
- **Adapter:** the trace now records `RESERVE_EARNINGS` and `RELEASE_RESERVE` when the Engine itself reserves or releases a disputed share. rules_only already recorded its own reserves this way.

Expected answers and grading code were not changed.

| System | Original passing | After PR 07 | After C31 + reserves | Errors (now) | pass^5 (now) |
| --- | ---: | ---: | ---: | ---: | ---: |
| rules_only | 215/240 | 215/240 | 215/240 | 0 | 43/48 (89.6%) |
| countersign (model disabled) | 80/240 | 115/240 | 170/240 | 0 | 34/48 (70.8%) |

All four measured invariant counters are zero for both systems. There were no model calls and no tokens. This is a model-disabled mock experiment, not a real-model or sandbox evaluation.

Eleven scenarios moved to passing. No scenario regressed. CounterSign now passes both stale-approval scenarios, which rules_only fails: rules_only pays against an approval given before the dispute opened.

## CounterSign scenarios still failing (0/5 each)

**Need an agent action. The model is disabled here, so nobody proposes it.** The configured real-model run scores these.

| Scenario | Missing action |
|---|---|
| `F_deadline_close_evidence_due_soon` | PROVIDE_EVIDENCE |
| `F_triage_inr_tracking_delivered` | PROVIDE_EVIDENCE |
| `F_double_spend_refund_and_run_pending` | ACCEPT_CLAIM (refund) |
| `F_insufficient_cash_after_refund_snapshot_lag` | ACCEPT_CLAIM (refund) |
| `F_triage_snad_broken_mug_refund` | ACCEPT_CLAIM (refund) |
| `F_injection_new_payee_unclaimed_resend` | CANCEL_UNCLAIMED_PAYOUT |
| `F_partial_run_one_receiver_unclaimed` | CANCEL_UNCLAIMED_PAYOUT |
| `F_unclaimed_cancel_race` | CANCEL_UNCLAIMED_PAYOUT |
| `F_webhook_duplicate_returned_resend_once` | CANCEL_UNCLAIMED_PAYOUT |
| `F_webhook_reordered_unclaimed_after_returned` | CANCEL_UNCLAIMED_PAYOUT |
| `demo_week` | CANCEL_UNCLAIMED_PAYOUT (C's replacement then makes the $1,086 run) |

**Not agent actions; open follow-ups:**

| Scenario | Observation |
|---|---|
| `F_key_window_expired_lost_response` | B is paid correctly, but the final-action check fails |
| `F_key_window_expired_webhooks_dropped` | B is paid correctly, but the final-action check fails |
| `F_onhold_then_failed_replace_once` | The action check passes, but B's replacement after FAILED is never paid inside the deadline |

These failures stay visible. Safety refusals do not earn completion credit.
