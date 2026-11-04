# Real AI agent week: stage one

The owner's Nov 5 addendum replaces the new-run provider with the local CLI.
Only stage one is authorized: provider, a two-turn application read-tool round
trip, and one file-injection canary, then stop. No week, pilot or evaluation.

The provider is implemented in `src/countersign/agent/codex_cli.py`. It uses one
ephemeral CLI process per model turn, JSON output schemas for the existing
application read-only allowlist, a new empty temporary working directory outside
the repository and a minimal OS/login-location environment. It never reads or
copies auth files or loads environment files. There are no automatic retries.

Configuration disables shell, web, MCP, apps, plugins, file/image tools and other
tool-producing features. The startup catalog retains the CLI's original model instructions,
with only shell/file-edit and experimental-tool capabilities removed. Read-only
sandboxing alone is not the tool boundary.

An offline wire probe sent the same configuration to an unauthenticated local
HTTP stub: **zero native tools** in the actual request. No model backend or login
was used. The installed CLI version is pinned; an unverified version or failed
wire proof refuses startup. See the source record under
`recordings/real_model/2026-11-04-c67-cli-stage1/offline-proof.json`.

The JSON event parser accepts text/reasoning and lifecycle/usage events only.
Any native command, file, web, MCP or unknown tool event kills the process and
fails the call. The stage-one runner records the run as failed and refuses an
automatic rerun. Quota/usage stops map to MODEL_QUOTA_EXHAUSTED.

The owner approved at most three live turns. The **first turn failed** after
3.469 seconds: the JSON item allowlist raised MODEL_NATIVE_TOOL_FORBIDDEN and
killed the process. **One turn attempted, no retries.** The read-tool round trip
did not complete; the application executed no read. The canary was **not run**.
No week, pilot or evaluation was started. Subscription dollar cost and provider
usage are unavailable for the aborted call.

The exact rejected item type was not retained. This failure does not establish
which capability was attempted, nor certify native tools absent in an authenticated
run. The offline stub's zero-tool result is separate evidence. No new invocation
was made to investigate or improve the result. Stage one has **not passed**.
Exact identity, CLI version and date are in the run record and small Run details;
that line discloses that the CLI adds its own system instructions.

Public panel name: **Real AI agent week**. The previous free-provider attempt
is removed from the panel and remains in the decisions log. The Oct 27 run is
unchanged and correctly identified only in its Run details. No new scores exist.

Checks: 69 focused Python passed before two further subprocess-abort checks and
the run-failure test were added; final provider/runner selection **30 passed**.
All **134 frontend tests** passed after the failure-display regression was added.
The final publication follow-up passed 11 Python tests and seven panel tests.
Ruff and all four import contracts passed.
No full pytest suite, Docker build, week, pilot, evaluation, merge or deployment.

The existing owned preview on 8201 serves updated static assets without a new
image build. Nine browser captures under `stage1/` verify light, dark and phone,
public wording, dataset separation and missing-field disclosures, with no
horizontal overflow, runtime exceptions, warnings or non-GET requests.

Sources used for exact configuration: the installed CLI's help and bundled
catalog, the matching release's configuration schema and tool-registration source,
and the [official configuration reference](https://developers.openai.com/codex/config-reference).
