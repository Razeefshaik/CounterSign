# Real AI agent: stage-one record

**Result: failed on the first CLI attempt.** The event allowlist rejected an
item with MODEL_NATIVE_TOOL_FORBIDDEN and terminated the child process.
The exact rejected item type was not retained, so this record cannot identify
which capability triggered the rejection.

- Owner cap: three turns. Attempted: one. Retries: zero.
- Read-tool round trip: failed before the application executed a read.
- Canary: not run after the abort.
- Week, pilot and evaluation: not run.
- Provider token usage and subscription dollar cost: unavailable for the abort.

The offline unauthenticated local-stub proof advertised zero native tools. That
is separate from the failed authenticated attempt and does not certify native
tools absent in that run. Stage one has not passed. No invocation was repeated
to diagnose the event or obtain a better result.

[Run details](run.json) contains the exact requested model identity, CLI version
and timestamp. **The CLI adds its own system instructions.** Prompts, raw CLI
outputs, login files, environment values and canary strings are not published.
No PayPal calls or application writes occurred in this stage.
