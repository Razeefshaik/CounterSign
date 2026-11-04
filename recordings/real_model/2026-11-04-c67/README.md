# C67 model evidence, November 4, 2026

This folder records a prerequisite stop, not a completed model week.

- Requested free model: `google/gemma-4-31b-it:free`, **Gemma via OpenRouter**. Gemma is not Gemini.
- The owner selected free models after the exact Gemini smoke passed. OpenRouter's public catalog listed no free Gemini model.
- Free smoke: three HTTP requests, all **429**, `MODEL_QUOTA_EXHAUSTED`. Zero successful model replies; the tool-result round trip did not pass. Recorded cost ceiling: **$0**. Provider quota diagnostics were not retained because raw error text can contain private information.
- The new mock week and the 48-scenario comparison were **not started**. There are no new proposals, rationales, flags, shield verdicts or scores to show. No alternate model or rerun was attempted.
- The earlier paid smoke succeeded with exact `google/gemini-3.5-flash`: two requests, 270 input and 135 output tokens, reported cost **$0.00162**. This is a harmless local tool stub, not financial evaluation or a demo week.
- Public smoke metadata and request accounting: `design-demos/reference/c67/{smoke.json,smoke-budget.json,gemma-smoke.json,free-budget.json}`.
- The October 27 Gemini week remains unchanged. It used `gemini-3.5-flash-lite` via Google on mock PayPal and ended `COMPLETE_WITH_GAPS`.

No PayPal calls occurred in either smoke. Only `OPEN_ROUTER_API_KEY` was selectively loaded at runtime into the model client; no key or unrelated environment settings were exported. Resume requires a new owner instruction after the quota/access condition changes. Do not rerun for a better result.
