# M13: the plain LLM baseline (mock only)

The evaluation compares three systems. This page explains the first one: a plain
LLM agent that operates PayPal directly, with no CounterSign safety layer. It is
the "what if you just gave a model the PayPal tools" comparison, and it is built
to be fair, not to fail on purpose.

## What it is

`eval/systems/baseline_llm.py` gives a chat model ten tools and lets it work:

| Tool | What it does on the mock |
| --- | --- |
| `get_balances` | Reads the account balance |
| `list_disputes`, `get_dispute` | Reads disputes, buyer messages and allowed actions |
| `get_payout_batch`, `get_payout_item` | Reads payouts and their status |
| `create_payout` | Sends money to a maker (money moves) |
| `cancel_payout_item` | Cancels an UNCLAIMED payout (money comes back) |
| `provide_evidence` | Sends seller evidence on a dispute |
| `accept_claim` | Refunds a buyer (money moves) |
| `request_owner_approval` | Asks the owner, through the same owner policies every system gets |

The model is told the owner's business rules in plain words: pay each verified
maker their share once, don't pay disputed orders, ask the owner before a payout
run, a replacement or a refund, and treat buyer text as untrusted. Whether it
follows those rules is up to the model. Nothing in code checks it: there is no
shield, no ledger and no approval tied to an exact payload. That is the point of
the comparison.

## It is fair: PayPal's own idempotency, used correctly

DECISIONS says the baseline must use PayPal's documented idempotency (otherwise
we would only prove careful beats careless). So:

- Every intended payout gets a **reference** chosen by the model, such as
  `run-2026-11-02-B`. The tool turns it into one fixed `sender_batch_id` and the
  same `PayPal-Request-Id`, and freezes the request body.
- If the network drops (the F01 fault), the tool reports `UNKNOWN`. The model is
  told to retry with the same reference, and the tool sends the identical request.
- PayPal answers a repeat with `400 USER_BUSINESS_ERROR` and a link to the
  original batch. The tool follows that link with a GET, checks the batch carries
  our `sender_batch_id`, and reports `ALREADY_SUBMITTED`. No second payment.
- Reusing a reference for a different maker or amount is refused before PayPal is
  called.

The model names a maker key, never an email. The tool takes the email from the
owner's maker records, so buyer text cannot redirect a payout by itself. The
model can still pay the wrong maker or the wrong amount, because nothing checks.

## It can never touch the real sandbox

- It refuses any mock URL that is not `http://localhost`, `127.0.0.1` or `::1`.
- It refuses a scenario whose ground is not `mock`.
- It refuses credentials that are not the runner's fake `mock-` values.
- It reads only the scenario's `initial_state` (without the mock seed), never the
  expected answers, future steps or ground truth.
- The model never sees an email address or a credential. Model text is never
  printed, saved or shown to the owner.

## Model, tokens and quota

The model is passed in (`env.llm`) and uses the same interface CounterSign's
agent uses, so both model systems can be given the **same explicitly chosen
model**. Every call is counted, with input and output tokens from the model's own
usage report. A model that reports no usage is an error, not a zero.

If the provider says the quota is used up (HTTP 429), the baseline stops calling
the model. The run is labelled `MODEL_QUOTA_EXHAUSTED`, and the whole comparison
stops. It writes only `runs.partial.jsonl`, never a summary, because a
half-finished comparison would be misleading.

## Mock over real TCP

CounterSign's agent reads PayPal through a separate toolkit process, which cannot
use the runner's in-process mock. The runner now has a `mock_transport="tcp"`
option. It serves the same mock on a random free port on `127.0.0.1` for the run,
then shuts it down. The default is still in-process. Tests show the results
match across both transports, and that CounterSign's F01 passes over TCP.

## How to run it

Tests (fake models only, no key needed):

```powershell
$env:PYTHONPATH = "src;."
python -m pytest tests/eval/test_baseline_llm.py tests/eval/test_tcp_runner.py
```

A real-model comparison (owner only, costs model quota). The command reads only
`COUNTERSIGN_LLM_PROVIDER`, `COUNTERSIGN_LLM_MODEL` and `GOOGLE_API_KEY` from the
file you name, and never any PayPal value:

```powershell
python -m eval.systems.baseline_llm --output eval/results/m13-trial --k 1 `
  --env-file .env `
  --countersign-database-url postgresql://countersign:countersign-dev@127.0.0.1:55433/countersign_m13_eval
```

The CounterSign database must be a migrated, disposable database whose name ends
in `_eval` or `_test`. Leave out `--countersign-database-url` to run the baseline
alone. Add scenario paths at the end to run fewer scenarios.

## What it does not prove yet

No real model has been run through it. The fake models in the tests show the
plumbing, the idempotency and the safety refusals work. They say nothing about
how well a real model does; that is M18's job.
