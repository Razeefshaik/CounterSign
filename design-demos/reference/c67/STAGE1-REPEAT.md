# Real AI agent: stage-one repeat stopped

The five requested fixes are committed: deprecated aliases removed with the
replacement permission gate disabled; startup error items counted only before
turn start; error items after turn start fatal; rejected item types allowlisted;
and the real read schemas checked and adapted only at the output boundary.
Command items before turn start still abort. Warning text is never retained.

All eight real read schemas were audited offline. Five have optional properties.
The output adapter requires every property while preserving omission, explicit
null and defaults, then validates decoded arguments against the original schema.
No tool or allowlist was widened. The unauthenticated loopback wire proof still
advertised zero native tools. Preflight passed 50 focused tests and scoped Ruff.

The one approved repeat **failed**. Its first CLI invocation returned the expected
read request in 10.875 seconds with no native tool event, and two startup warnings
counted without text. The second application attempt failed locally at continuation
serialization before another CLI invocation. The fixed local observation was not
delivered to the model. The round trip failed; the canary was not run.

An offline synthetic replay confirms that the client returns SimpleNamespace but
its continuation serializer expects model_dump(). This diagnosis used no model
or subprocess. No subsequent feature fix, retry or model invocation was made.
One real model invocation, two application attempts, zero retries. No security
stop detected, no week, pilot, evaluation, PayPal call or application write.

The generated run record remains as written, including stale first-invocation
warning metadata in its second slot. The linked post-run audit explains that
limitation. Exact identity/version/date stay in the run record and small Run
details, with the CLI-owned-instructions disclosure. The old failed folder is
unchanged. The public panel is still named **Real AI agent week**, now linked to
this repeat and labelled with application attempts rather than model turns.

Evidence: [repeat recording](../../../recordings/real_model/2026-11-04-c67-cli-stage1-repeat/README.md),
[offline preflight](stage1-repeat/README.md), and [earlier attempt](STAGE1.md).

Final publication checks: 11 Python tests and all 135 frontend tests passed;
Ruff passed and all four import contracts were kept. No full pytest, new Docker
build or new browser captures. Two static assets were copied to the existing
owned preview on 8201; both return 200 and match the source bytes. See
[publication check](stage1-repeat/publication-check.json). The original failed
folder and Oct 27 recording have no diff from the previous commit.

Shared files touched in this repeat: docs/DECISIONS.md, docs/AGENT_STATE.md and
docs/AGENT_FUTURE_NOTES.md. No contract, dependency, README or financial-rule change.

Stop here. A continuation serializer fix and another live attempt need a new
owner instruction. No stage two, merge or deployment.
