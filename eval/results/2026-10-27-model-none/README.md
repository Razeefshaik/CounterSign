# Model-disabled mock evaluation

48 scenarios x 2 systems x k=5 = 480 completed cells. Source: `e2f5687`.
The recorded source fingerprint binds benchmark, core, mock and migration code.
After the run, an unused Ruff suppression comment was removed from the
CounterSign adapter; its Python AST was verified identical to the evaluated code.

One orchestration interruption occurred after 410 completed cells; the initial
CLI boundary printed no raw exception. An identical checkpoint resume completed
the remaining 70 cells without rerunning completed cells. The cause was not
established and is not classified as a quota stop or a scored system failure.

All results are local mock observations, with no model provider or PayPal calls.
CounterSign's model was disabled; baseline_llm was not run. Zero invariant
counters on errored rows are not evidence of safety. See report.md for metrics
and known disputed-share allocation limits.
