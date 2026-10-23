# D2: three payments and tracking

## 1. What this check proves

This creates three sandbox payments: $400 for Maker A, $60 for Maker B and $25 spare.
It saves their capture IDs so the later dispute checks use these same payments.
It also tests adding and reading tracking for the $400 order.

## 2. What you (the owner) will do in the browser

Open each approval link printed by the script. Log in as **buyer-1** and approve
the payment, then press Enter in the terminal. These are staged sandbox actions.
Enter a test carrier code (for example USPS) and a test tracking number when asked.
No real parcel is sent; these are merchant-supplied test details.

## 3. What the script does, step by step

1. Read the starting USD balance.
2. Save a request ID and body before creating each order.
3. Wait for your approval and confirm it with an order GET.
4. Capture the three payments and save their fees, net amounts and capture IDs.
5. Add tracking through Orders v2 and read the shipping-tracker endpoint.
6. Observe balances every five minutes for up to three hours, stopping once the
   reported available balance crosses the expected net-payment threshold.
7. Save findings and any capture webhooks already received. D11 and D12 later
   analyse webhook delivery and balance freshness.

## 4. What gets saved, and where

Under recordings/sandbox/<UTC-date>/D2/, state.json keeps progress and
findings.json gives the result. Numbered JSON files hold redacted HTTP exchanges.
Capture IDs are at data.captures.inr_400.capture_id, snad_60.capture_id and
spare_25.capture_id; tracking is at data.tracking.inr_400.
Timeline events use the net amounts after fees. Missing balance data is saved
honestly; a failed read is never interpreted as a balance change.

## 5. How the verdict is decided

- **CONFIRMED:** all three captures report COMPLETED and tracking upload succeeds.
- **BLOCKED:** a capture is not completed or the tracking write cannot be confirmed.
- **DIFFERENT:** not used as an overall D2 verdict; tracking fields, fees and timings
  are saved for the later analysis checks.

A lost response stops the run with progress saved. Resume with the same recordings
root and UTC date. Order/capture recovery keeps the original request ID and body;
an identical replay requires your confirmation. Tracking is never automatically
resubmitted after an ambiguous response.

## 6. How to run it

After D1 is CONFIRMED and the webhook catcher is running:

~~~powershell
.venv\Scripts\python.exe -m scripts.sandbox_checks run D2
~~~

This command makes real **sandbox** calls when you run it. Do not use --fresh
to recover an interrupted payment: it discards progress and can create new orders.
The implementation was tested with stubbed HTTP; no sandbox calls were made while building it.

Request shapes follow [PayPal's Orders v2 tracking endpoint](https://developer.paypal.com/api/orders/v2/orders-track-create/).
