# CounterSign: Sandbox Findings (day one, Oct 23 to 25, 2026)

One row per check from `docs/PLAN.md` section 1.3. Verdict is CONFIRMED (matches the handoff), DIFFERENT (design change needed; add a DECISIONS.md entry) or BLOCKED (could not be tested; recorded, not chased). Evidence files live under `recordings/sandbox/`.

| ID | Handoff claim | Observed | Evidence file | Verdict | Design impact |
| --- | --- | --- | --- | --- | --- |
| D1 | App token has payouts, disputes and reporting scopes | | | | |
| D2 | Buyer can pay the marketplace; tracking can be added and read back | | | | |
| D3 | Single-item payout to a registered maker reaches SUCCESS | | | | |
| D4 | Payout to an unregistered email returns UNCLAIMED reliably; unclaimed money excluded from available | | | | |
| D5 | Cancel works and the item shows RETURNED on GET | | | | |
| D6 | Identical replay is safe and resolves UNKNOWN; duplicate sender_batch_id links to the original | | | | |
| D7 | Payout negative test values produce the documented errors | | | | |
| D8 | Disputes can be created (API or Resolution Center); PayPal holds the funds | | | | |
| D9 | Evidence can be submitted on an item-not-received dispute (record which stage) | | | | |
| D10 | Settle works from UNDER_REVIEW; buyer close is a fallback | | | | |
| D11 | Webhook delivery delays, duplicates, signature verification | | | | |
| D12 | Balance freshness (up to 3 hours) | | | | |
| D13 | Agent Toolkit exposes only the allowlisted read tools | | | | |
