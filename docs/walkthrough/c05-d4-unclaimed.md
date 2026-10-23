# D4: three unclaimed payouts

## 1. What this check proves

We send $1 to each of three addresses that have never been registered as sandbox
accounts. We test whether all three become UNCLAIMED, and record what happens
to available and withheld balances.

## 2. What you (the owner) will do in the browser

Nothing normally. Keep all three addresses unregistered. Do not create an account
for unreg-3 unless you later choose D5's optional claim race.
An interrupted payout may ask you to confirm an identical replay in the terminal.

## 3. What the script does, step by step

1. Verify that three unregistered recipient addresses are configured.
2. Read the starting balance.
3. Save each exact request body, hash and keys, then send three separate $1 payouts.
4. Poll each batch and item every 30 seconds for up to 30 minutes.
5. Save every observed status and the corresponding timeline events.
6. Read balances every five minutes for up to three hours, stopping when the
   available balance crosses the submitted-amount threshold.
7. Compare available and withheld with the starting snapshot and save findings.

## 4. What gets saved, and where

recordings/sandbox/<UTC-date>/D4/ contains state.json, findings.json and HTTP recordings.
data.payouts has unreg_1, unreg_2 and unreg_3, each with batch_id, item_id,
item_status and status history. D5 uses these saved IDs.
The recordings replace recipient emails with aliases.

## 5. How the verdict is decided

- **CONFIRMED:** all three item GETs show UNCLAIMED and balance fields are available.
- **DIFFERENT:** one or more observed item states differ from UNCLAIMED.
- **BLOCKED:** required addresses, usable item states or balance evidence are missing.

The unclaimed result and balance result are also recorded separately. A failed
balance read never means that withheld stayed unchanged. Snapshot differences
are observations; D12 checks their freshness and overlap with other activity.

## 6. How to run it

~~~powershell
.venv\Scripts\python.exe -m scripts.sandbox_checks run D4
~~~

D1 must be CONFIRMED. Resume with the saved directory and date, without --fresh.
Leave unreg-2 uncancelled: D5 includes its longer observation.
Development tests use fake HTTP; this command makes sandbox calls when you run it.
