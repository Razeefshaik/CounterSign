# D6: safe replay and lost responses

## 1. What this check proves

We test what happens when payout requests are repeated, altered or sent together.
The key recovery case is an UNKNOWN write: its response was lost, so we reconcile
using the exact saved request rather than creating a new payment.

## 2. What you (the owner) will do in the browser

No browser action is required. Maker B must already be a registered sandbox
recipient. All five experiments are staged sandbox actions with $1 per experiment.

## 3. What the script does, step by step

1. Save the starting balance. Give each experiment fresh keys and a separate $1 attempt.
2. **A:** send a payout and repeat exactly the same body and request ID.
3. **B:** keep the sender batch ID, change the request ID, and follow the error's
   sandbox batch link. Verify that batch's payee, amount and sender item ID.
4. **C:** reuse the request ID but change only the note. Read the item to see which
   body won. Amount and recipient remain unchanged.
5. **D:** send two POSTs concurrently with the same keys and body, then read the
   batch and item to verify exactly one payment. Keep both HTTP results.
6. **E:** use a 50 millisecond read timeout. If the result is unknown, record it,
   replay the exact saved request and confirm one item by GET and balance.
7. Save the result of each experiment and an overall verdict.

## 4. What gets saved, and where

recordings/sandbox/<UTC-date>/D6/ contains state.json, findings.json and numbered
HTTP recordings. data.experiments has a, b, c, d and e, including both requests'
results, linked batch IDs, item observations and individual verdicts.
Before each POST, data.writes saves its request ID, redacted exact body, body hash
and UNKNOWN state. Timeline events feed D11 and D12.

## 5. How the verdict is decided

- **CONFIRMED:** the expected replay/link behaviour is observed, the original body
  wins C, D confirms one successful item, and E reproduces an ambiguous response
  then confirms one item and the observed balance threshold.
- **DIFFERENT:** usable evidence shows different behaviour, such as another body
  winning or more than one batch being returned.
- **BLOCKED:** evidence is missing, a pair was interrupted, or the 50 ms request
  returned normally and therefore did not reproduce a lost response.

The script never pretends a fast response was lost. The 50 ms timeout is real
when running against the sandbox. A linked batch is not adopted just because
its URL was returned; identity checks must pass.

## 6. How to run it

After D1 is CONFIRMED:

~~~powershell
.venv\Scripts\python.exe -m scripts.sandbox_checks run D6
~~~

An interrupted run resumes with its original keys. Use the same recordings root
and --date YYYY-MM-DD; do not use --fresh for recovery. D6 deliberately repeats
requests as its experiment; ordinary payout checks do not retry automatically.
Development tests simulate PayPal and advance a fake clock; no real API calls
were made during implementation.

See [PayPal's duplicate-request guidance](https://developer.paypal.com/docs/payouts/standard/integrate-api/customize/).
