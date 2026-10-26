# CounterSign: Sandbox Findings (day one, run Oct 23, resumed Oct 24 and Oct 26, 2026)

One row per check from `docs/PLAN.md` section 1.3. The verdict is one of:

- **CONFIRMED:** matches the handoff;
- **DIFFERENT:** a design change is needed, with a DECISIONS.md entry;
- **BLOCKED:** could not be tested; recorded, not chased.

Evidence lives under `recordings/sandbox/`. `recordings/sandbox/INDEX.md` maps each check to its files. Each check's analysed results are in `recordings/sandbox/2026-10-23/<check>/state.json`.

## Summary

| ID | Verdict | One line |
| --- | --- | --- |
| D1 | CONFIRMED | Every needed scope is present, including `disputes/create` |
| D2 | CONFIRMED | Orders captured with fees; tracking written through Orders v2 and read back |
| D3 | CONFIRMED | Payout reaches SUCCESS in about 30 s; the create response has no item IDs |
| D4 | CONFIRMED | All three unregistered payouts reach UNCLAIMED; available drops at once |
| D5 | CONFIRMED | Cancel returns RETURNED immediately; repeat and wrong-state cancels give named errors |
| D6 | **DIFFERENT** | Every duplicate POST returns 400 with a link to the original batch, never a replayed 201 |
| D7 | CONFIRMED | All 14 payout negative-test values behave as documented |
| D8 | **DIFFERENT** | Dispute creation through the API is blocked; disputes freeze captures (PENDING), never `withheld` |
| D9 | **DIFFERENT** | Evidence is possible only while WAITING_FOR_SELLER_RESPONSE with a `provide_evidence` link |
| D10 | CONFIRMED | All three settlement paths work; three real outcome codes recorded |
| D11 | CONFIRMED (F11 DIFFERENT) | All 46 deliveries verified; simulator events verify too |
| D12 | **DIFFERENT** | Lag is under 3 h and the sums always add up, but disputes never touch `withheld` |
| D13 | CONFIRMED | The toolkit exposes exactly the 5 allowlisted read tools |

## Details

| ID | Handoff claim | Observed | Evidence | Verdict | Design impact |
| --- | --- | --- | --- | --- | --- |
| D1 | App token has payouts, disputes and reporting scopes | The token carries payouts, disputes read, disputes update-seller, **disputes create** and reporting scopes. None was missing. | `2026-10-23/D1/` | CONFIRMED | None. Having the create scope did not make API dispute creation work (D8). |
| D2 | Buyer can pay; tracking can be added and read back | Three orders were captured ($400, $60, $25). The $400 capture: fee $14.45, net $385.55. `PAYMENT.CAPTURE.COMPLETED` arrived about 9 s after capture. Orders v2 `track` returned 201. `GET /v1/shipping/trackers` returned USPS, `SHIPPED`, `CARRIER_PROVIDED`. | `2026-10-23/D2/`, `webhooks/` | CONFIRMED | None. Tracking is **merchant-uploaded** (F10); evidence wording must not claim carrier confirmation. |
| D3 | Single-item payout to a registered maker reaches SUCCESS | The 201 create response carried **no item IDs** (F1). The batch went PENDING to SUCCESS in about 32 s. Webhooks were PROCESSING, then SUCCESS, each about 10 s after the change. | `2026-10-23/D3/`, `2026-10-24/D3/` | CONFIRMED | Confirms F1: the item ID comes from GET batch. |
| D4 | Unregistered email gives UNCLAIMED reliably; unclaimed money is excluded from available | All three items reached UNCLAIMED (`RECEIVER_UNREGISTERED`). Available dropped by $3.75 (3 × $1 plus a $0.25 fee each). Withheld did not change. | `2026-10-23/D4/`, `2026-10-24/D4/` | CONFIRMED | Confirms F4, so the demo's "already out" Monday balance holds. |
| D5 | Cancel works and GET shows RETURNED | The cancel response (200) already shows `transaction_status: RETURNED`, and GET agrees. Cancelling again gives 400 `ITEM_ALREADY_CANCELLED`. Cancelling a SUCCESS item gives 400 `ITEM_INCORRECT_STATUS`. A `PAYMENT.PAYOUTS-ITEM.RETURNED` webhook arrived. The returned dollar showed in available within about 1.7 h. | `2026-10-24/D5/`, `2026-10-26/D5/` | CONFIRMED | Confirms F2 (RETURNED, not CANCELED). Contract 1.0.3 marks the cancel response confirmed. |
| D6 | An identical replay is safe and resolves UNKNOWN; a duplicate `sender_batch_id` links to the original | **Every** duplicate (exact replay, new request ID, changed body, losing concurrent POST) returned **400 `USER_BUSINESS_ERROR`** "Batch with given sender_batch_id already exists". The link to the original batch is in **`details[].link`** on host `api.sandbox.paypal.com`. A 50 ms-timeout lost response was resolved by following that link: one item, SUCCESS, $1, the same receiver. The harness recorded BLOCKED only because its link parser missed the nested link; batch `53YFFR9A9YFEA` was confirmed by hand. | `2026-10-24/D6/` (`e_confirm_linked_batch`) | **DIFFERENT** | C19b follows `details[].link` with a single host alias; a duplicate is never REJECTED. Contract 1.0.3 changes the mock to match. |
| D7 | Payout negative-test values produce the documented errors | All 14 values matched (batch PENDING, PROCESSING and DENIED; item CLOSED_MARKET, RECEIVER_UNREGISTERED, RISK_DECLINE; create and cancel errors). | `2026-10-24/D7/` | CONFIRMED | Fixtures for the mock's fault library (M14). |
| D8 | Disputes can be created (API or Resolution Center); PayPal holds the funds | **API creation is BLOCKED**: 400 "No permissions to set target_client_id", even with the scope present. The Resolution Center route works. Disputes appeared in `GET /v1/customer/disputes` 190 to 256 s after the buyer submitted them, and `CUSTOMER.DISPUTE.CREATED` arrived about 4 s after creation. Disputes match captures through `disputed_transactions[].seller_transaction_id`. The $400 item-not-received dispute went **straight to UNDER_REVIEW / CHARGEBACK**; the $60 one to WAITING / INQUIRY, and the $25 one to WAITING / CHARGEBACK. **Hold:** `withheld_balance` never rose (0 of the expected $485 over 37 readings), but all three captures turned **PENDING**. | `2026-10-23/D8/`, `2026-10-24/D8/` | **DIFFERENT** | Disputes are staged through the Resolution Center (F5, labelled as staged). A hold appears as the capture status, not as `withheld`. Cash keeps using available only, and DISPUTE_EARMARK stays our own non-cash reserve. |
| D9 | Evidence can be submitted on an item-not-received dispute | The $400 dispute (UNDER_REVIEW / CHARGEBACK) had only `provide_supporting_info` and `self` links: **no `provide_evidence`**. Evidence is possible only while WAITING_FOR_SELLER_RESPONSE **with** the link. On the $25 dispute, a multipart submission at that stage returned 200 (D10). | `2026-10-24/D9/`, `2026-10-24/D10/` | **DIFFERENT** | C24's shield allows autonomous evidence only with WAITING plus the link. The demo shows the shield block when the link is absent (C25). |
| D10 | Settlement works from UNDER_REVIEW; buyer close is a fallback | **Adjudicate** SELLER_FAVOR (sandbox control, 200) gave `RESOLVED_SELLER_FAVOUR` / `INELIGIBLE_BUYER_PROTECTION_POLICY`. **Accept-claim** as JSON (200) gave `RESOLVED_BUYER_FAVOUR` / `SELLER_ISSUED_REFUND`, `amount_refunded` $60, plus a `PAYMENT.CAPTURE.REFUNDED` webhook. **Evidence** as multipart (200) at WAITING / CHARGEBACK moved the dispute to UNDER_REVIEW. **Buyer close** gave `CANCELED_BY_BUYER` / `BUYER_CANCELLED_CASE`. | `2026-10-24/D10/`, `2026-10-26/D10/`, `outcome_codes.json` | CONFIRMED | `OUTCOME_MAP` uses exactly these three codes, with British "FAVOUR". The default formats are multipart evidence and JSON accept-claim. The refund source is `paypal:available`, because withheld was never used (D12). |
| D11 | Webhooks verify through the API; deliveries can be late, duplicated or missing | All 46 real deliveries verified `SUCCESS`. Median delay was 10.5 s (dispute events about 4 s). One outlier of about 2.7 days arrived while the catcher was offline. There was 1 duplicate, 1 out of order and 1 missing. `GET /v1/notifications/webhooks-events` works (47 listed, 2 never delivered to us), and resend works. **The simulator event also verified `SUCCESS`.** | `2026-10-26/D11/`, `webhooks/` | CONFIRMED (F11 DIFFERENT) | Webhooks only trigger a GET; the event list is a backfill source (C15). F11 was wrong: simulator events can verify. We still verify every event, and the mock keeps its test signer. |
| D12 | Balances can lag up to 3 h | `last_refresh_time` and `as_of_time` lag by 1.4 to 2.4 h (median about 1.7 h), never over 3 h. available + withheld = total in all 585 readings. Payouts and returns appear in available within about 1.5 h. **No dispute event ever moved `withheld`**, and resolutions did not either. Transaction Search lag: BLOCKED (recorded, not chased). | `2026-10-23/D12/`, `2026-10-24/D12/`, `2026-10-26/D12/`, `balances.jsonl` | **DIFFERENT** | Confirms F12's formula: subtract our own outflows after `last_refresh_time`. The demo must not narrate "$400 withheld"; it shows the disputed capture as PENDING and our reserve instead. |
| D13 | Agent Toolkit exposes only the allowlisted read tools | With `paypal-agent-toolkit` 1.11.0 and langchain 0.3.23, the tool set is exactly `get_dispute`, `get_order_details`, `get_shipment_tracking`, `list_disputes`, `list_transactions`. Nothing unexpected, nothing missing. | `2026-10-24/D13/` | CONFIRMED | The startup allowlist check stays (C23). The toolkit now runs in its own `.venv/toolkit` (C23b). |

## Still open

- **Transaction Search lag (D12):** BLOCKED. It was recorded and not chased.
- **Exact cash attribution of dispute resolutions.** Available rose by $383.07 between the 12:00 and 12:30 UTC refreshes on Oct 24, after the $400 seller-favour ruling and the $60 refund. The individual events overlap, so the reflection rows could not attribute that change. The design does not depend on it: cash uses available plus our own outflows only.
