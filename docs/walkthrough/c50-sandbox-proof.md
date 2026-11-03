# C50: recorded sandbox proof

`#proof` presents committed evidence, without calling PayPal or an AI provider.
The persistent simulated banner describes the running demo. The proof report
describes historical PayPal sandbox recordings from Oct 23–26, 2026; the Gemini
week on Oct 27 is explicitly separate and ran against the local mock.

Build or verify the artifact from the repository root:

```powershell
python scripts/build_sandbox_proof.py
python scripts/build_sandbox_proof.py --check
```

The builder uses the findings and index tables, numbered D1–D13 exchanges and
the real-model README/run log. Each fact carries a source path; counts list the
contributing paths and their definition. No timestamp from the build machine is
included. Identical evidence gives byte-identical UTF-8 output. The browser
reads `web/results/sandbox-proof.json`; recordings need not ship in Docker.

The summary counts 1,041 exchange attempts, including 34 transport errors and
1,007 actual HTTP responses. It counts 47 successful signature API responses,
including the simulator event, rather than claiming 47 delivered real events.
Six distinct payout items were observed SUCCESS, excluding D7 negative-test
fixtures. Three disputes have recorded outcome codes, including buyer close.
Per-check resource statuses are the latest observed within that check, not a
claim about today's PayPal status. No capture, fee, time, outcome or ID is filled
in when it is absent from the evidence.

The Gemini card preserves `COMPLETE_WITH_GAPS`. The model chose WAIT for the
injection and settled-retry prompts; the real-model recording does not show
shield BLOCK verdicts for them. Evidence/cancel outcomes sourced from the README
are distinguished by their source links from direct verdicts in `run.jsonl`.

The output guard refuses credential fields, recognizable credential/token strings,
and email addresses outside synthetic `@example` aliases before replacing any
artifact. The builder selects public IDs/status fields and never copies full
HTTP bodies, token responses, headers or model free text into the bundle.

To rerun **real sandbox checks**, use the approved instructions and manual steps
in `docs/PLAN.md` section 1 and the local command help:

```powershell
python -m scripts.sandbox_checks --help
```

Those checks require the owner's separate sandbox setup and explicit execution;
C50 did not run them or read local credentials. See `docs/SANDBOX_FINDINGS.md`
for prerequisites, known differences and untested cases. Tracking is merchant
uploaded, already-available capture disputes remain untested, and Transaction
Search lag was blocked.
