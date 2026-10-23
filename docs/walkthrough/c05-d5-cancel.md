# D5: cancel and confirm a return

## 1. What this check proves

We cancel D4's unreg-1 payout and check that GET confirms RETURNED.
We also record the errors for cancelling it again and trying to cancel D3's
successful payment. A second unclaimed payout is left alone as a control.

## 2. What you (the owner) will do in the browser

No browser action is needed for cancellation. You can choose the optional
unreg-3 test: create a sandbox Personal account for that address and claim the
payment. The script asks first; type no to skip. Creating this account means
the address can no longer be used as a never-registered test address.

## 3. What the script does, step by step

1. Load D3 and D4's saved item IDs and read the starting balance.
2. GET unreg-1 and cancel only when that GET shows UNCLAIMED.
3. Poll GET for RETURNED. An ambiguous cancel is reconciled by GET, never retried.
4. Deliberately try the two negative cancels, recording status and error name.
5. Observe available balance for the returned amount, for up to three hours.
6. Offer the optional unreg-3 claim test and save initial findings.
7. Keep reading unreg-2 every five minutes until **Oct 25, 23:59:59 IST**, or
   until it leaves UNCLAIMED for a terminal state. Never cancel this control.

## 4. What gets saved, and where

recordings/sandbox/<UTC-date>/D5/ contains state.json, findings.json and HTTP
recordings. It includes the cancel response, subsequent GET states, negative
error names, return timeline event, received webhook order and balance readings.
Initial findings are available before the long control monitor finishes.

## 5. How the verdict is decided

- **CONFIRMED:** GET shows RETURNED and both negative cancels receive 4xx errors.
- **DIFFERENT:** another item state is observed or the negative behaviour differs.
- **BLOCKED:** required D3/D4 IDs or usable item evidence are missing.

A webhook or cancel response alone never confirms the returned funds.
Balance lag is saved separately for D12.

## 6. How to run it

After D3 and D4:

~~~powershell
.venv\Scripts\python.exe -m scripts.sandbox_checks run D5
~~~

**Run this in a separate terminal.** After the main checks, it remains running
to monitor unreg-2 until the date above, while you can run other checks elsewhere.
If interrupted, resume with the same recordings directory and --date YYYY-MM-DD.
Do not use --fresh for recovery.
Development tests shorten the monitor using a fake clock and make no PayPal calls.

The expected cancel status is documented in
[PayPal's payout item reference](https://developer.paypal.com/api/payments.payouts-batch/v1/definitions/payout_batch_items_list).
