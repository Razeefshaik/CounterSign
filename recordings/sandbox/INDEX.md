# Sandbox recordings index

Redacted PayPal sandbox recordings from the day-one checks (run date 2026-10-23, resumed on
Oct 24 and Oct 26). Verdicts and design impact are in `docs/SANDBOX_FINDINGS.md`.

**Redaction:** tokens, client credentials and real emails were removed by the recorder;
role aliases replace emails. Tunnel hostnames read `TUNNEL.ngrok-free.app`. A secret scan
over every file found no private values before commit.

**Layout**

- `<date>/<check>/NN_<step>.json`: one HTTP exchange each (method, path, redacted headers,
  request and response bodies, `t_sent`/`t_recv`, `paypal-debug-id`). The date is the day the
  request was made, so a resumed check has exchanges under several dates.
- `2026-10-23/<check>/state.json`: the check's checkpoint and analysed results; `findings.json`
  where the check wrote one.
- `webhooks/`: every webhook delivery the catcher received (raw body plus headers).
- `balances.jsonl`: one `/v1/reporting/balances` reading about every 5 minutes.
- `outcome_codes.json`: every dispute outcome code seen in D10, with the path that produced it.

| Check | Topic | Verdict | Exchanges (by date) | Steps recorded |
| --- | --- | --- | --- | --- |
| D1 | Auth and scopes | CONFIRMED | 2026-10-23: 2 | `oauth_token`, `balance_snapshot` |
| D2 | Money in (orders, captures, tracking) | CONFIRMED | 2026-10-23: 40; 2026-10-24: 62 | `balance_snapshot` x45, `money_in_balance` x37, `oauth_token` x4, `approved_inr_400` x2, `baseline_balance`, `create_inr_400`, `create_snad_60`, `create_spare_25` (+10 more) |
| D3 | Payout happy path | CONFIRMED | 2026-10-23: 17; 2026-10-24: 3 | `balance_snapshot` x6, `payout_balance` x6, `oauth_token` x2, `maker_a_batch` x2, `maker_a_item` x2, `baseline_balance`, `maker_a` |
| D4 | UNCLAIMED payouts | CONFIRMED | 2026-10-24: 49 | `balance_snapshot` x19, `unclaimed_balance` x18, `oauth_token` x2, `baseline_balance`, `unreg_1`, `unreg_2`, `unreg_3`, `unreg_1_batch` (+5 more) |
| D5 | Cancel an unclaimed item | CONFIRMED | 2026-10-24: 144; 2026-10-26: 3 | `balance_snapshot` x113, `cancel_balance` x22, `oauth_token` x3, `baseline_balance`, `before_cancel`, `cancel_unreg_1`, `cancel_get`, `before_cancel_again` (+4 more) |
| D6 | Idempotency and lost responses | DIFFERENT (harness recorded BLOCKED; see SANDBOX_FINDINGS.md) | 2026-10-24: 70 | `balance_snapshot` x18, `lost_response_balance` x18, `oauth_token` x3, `a_base_batch` x2, `a_base_item` x2, `b_base_batch` x2, `b_base_item` x2, `c_base_batch` x2 (+16 more) |
| D7 | Payout negative-test values | CONFIRMED | 2026-10-24: 17 | `oauth_token`, `balance_snapshot`, `get_batch_errpyob002`, `get_batch_errpyob003`, `get_batch_errpyob004`, `get_batch_errpyo015`, `get_item_errpyo015`, `get_item_errpyo047` (+9 more) |
| D8 | Dispute creation and holds | DIFFERENT | 2026-10-23: 38; 2026-10-24: 167 | `balance_snapshot` x41, `hold_balance` x37, `hold_capture_inr_400` x37, `hold_capture_snad_60` x37, `hold_capture_spare_25` x37, `find_snad_60_list` x3, `oauth_token` x2, `find_spare_25_list` x2 (+9 more) |
| D9 | Evidence | DIFFERENT | 2026-10-24: 3 | `oauth_token`, `before_evidence`, `balance_snapshot` |
| D10 | Settlement paths and outcome codes | CONFIRMED | 2026-10-24: 127; 2026-10-26: 6 | `balance_snapshot` x113, `inr_400_poll` x4, `snad_60_poll` x4, `oauth_token` x3, `buyer_close_poll` x3, `spare_25_poll` x2, `inr_400_adjudicate`, `snad_60_accept_claim_json` (+2 more) |
| D11 | Webhooks and signature verification | CONFIRMED | 2026-10-23: 2; 2026-10-24: 2; 2026-10-26: 54 | `verify_signature` x47, `oauth_token` x4, `update_webhook_url` x2, `register_webhook`, `list_events`, `balance_snapshot`, `simulate_event`, `resend_event` |
| D12 | Balance freshness and reflection | DIFFERENT | 2026-10-23: 16; 2026-10-24: 180; 2026-10-26: 37 | `balance_snapshot` x220, `transaction_search` x8, `oauth_token` x4, `fresh_balance` |
| D13 | Agent Toolkit read-only allowlist | CONFIRMED | 2026-10-24: 2 | `oauth_token`, `balance_snapshot` |

**Webhook deliveries by event type**

| Event type | Deliveries |
| --- | --- |
| `CUSTOMER.DISPUTE.CREATED` | 3 |
| `CUSTOMER.DISPUTE.RESOLVED` | 2 |
| `CUSTOMER.DISPUTE.UPDATED` | 8 |
| `PAYMENT.CAPTURE.COMPLETED` | 5 |
| `PAYMENT.CAPTURE.REFUNDED` | 1 |
| `PAYMENT.PAYOUTS-ITEM.RETURNED` | 1 |
| `PAYMENT.PAYOUTS-ITEM.SUCCEEDED` | 6 |
| `PAYMENT.PAYOUTS-ITEM.UNCLAIMED` | 3 |
| `PAYMENT.PAYOUTSBATCH.PROCESSING` | 9 |
| `PAYMENT.PAYOUTSBATCH.SUCCESS` | 9 |

Balance readings: 580.
