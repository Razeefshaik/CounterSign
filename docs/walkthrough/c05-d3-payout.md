# D3: a payout to Maker A

## 1. What this check proves

A single $1 sandbox payout can reach Maker A. We compare the creation response,
the batch GET and the item GET, instead of treating a successful POST as payment settlement.

## 2. What you (the owner) will do in the browser

No browser action is normally needed. Maker A must already exist as a sandbox
recipient. If a previous write is UNKNOWN, the terminal asks you to confirm an
identical replay as a staged sandbox action.

## 3. What the script does, step by step

1. Read the starting balance and save the exact payout body, its hash and keys.
2. Submit one $1 payout to Maker A.
3. Poll the batch and item every 30 seconds for up to 30 minutes.
4. Verify the returned payee, amount and sender item ID match the saved request.
5. Record each observed state, timeline events, webhook deliveries and balances.
   Balance observation runs every five minutes for up to three hours or until the
   expected available-balance threshold is observed.

## 4. What gets saved, and where

recordings/sandbox/<UTC-date>/D3/ contains state.json, findings.json and numbered
HTTP recordings. data.payout contains batch_id, item_id and item_status for D5.
Saved bodies use email aliases. Their hashes prevent replay with a changed payee.
The create response is kept, including whether it contained item IDs.

## 5. How the verdict is decided

- **CONFIRMED:** an item GET confirms SUCCESS.
- **DIFFERENT:** PayPal reports another item state when observation ends.
- **BLOCKED:** no usable batch or item state, or a required setting is missing.

An identity mismatch stops the check for inspection. Missing or delayed balance
data is saved without claiming the money has settled; D12 analyses that evidence.

## 6. How to run it

After D1 is CONFIRMED:

~~~powershell
.venv\Scripts\python.exe -m scripts.sandbox_checks run D3
~~~

Resume an interrupted run with its saved date and directory; do not use --fresh.
Development tests use fake HTTP; this command makes sandbox calls when you run it.

Replay and duplicate-batch behaviour is checked against
[PayPal's Payouts integration reference](https://developer.paypal.com/docs/payouts/standard/integrate-api/customize/).
