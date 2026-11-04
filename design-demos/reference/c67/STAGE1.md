# Real AI agent week: stage one

The owner's Nov 5 addendum replaces the new-run provider with the local CLI.
Only stage one is authorized: provider, a two-turn application read-tool round
trip, and one file-injection canary, then stop. No week, pilot or evaluation.

The provider is implemented in `src/countersign/agent/codex_cli.py`. It uses one
ephemeral CLI process per model turn, JSON output schemas for the existing
application read-only allowlist, a new empty temporary working directory outside
the repository and a minimal OS/login-location environment. It never reads or
copies auth files or loads environment files. There are no automatic retries.

Shell, web, MCP, apps, plugins, file/image tools and other tool-producing features
are disabled. The startup catalog retains the CLI's original model instructions,
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

Live round trip and canary have **not run yet**: awaiting the pending reply to the
three-turn budget question required by the original prompt's Budget rule. The
planned cap is two round-trip turns plus one canary turn, using the existing CLI
login with no API key; subscription dollar cost is unavailable. Exact identity
will be recorded in `run.json` and shown only in its small linked Run details.

Public panel name: **Real AI agent week**. The previous free-provider attempt
is removed from the panel and remains in the decisions log. The Oct 27 run is
unchanged and correctly identified only in its Run details. No new scores exist.

Checks: 69 focused Python passed before two further subprocess-abort checks and
the run-failure test were added; final provider/runner selection **30 passed**.
All **133 frontend tests** passed. Ruff and all four import contracts passed.
No full pytest suite, Docker build, week, pilot, evaluation, merge or deployment.

The existing owned preview on 8201 serves updated static assets without a new
image build. Nine browser captures under `stage1/` verify light, dark and phone,
public wording, dataset separation and missing-field disclosures, with no
horizontal overflow, runtime exceptions, warnings or non-GET requests.

Sources used for exact configuration: the installed CLI's help and bundled
catalog, the matching release's configuration schema and tool-registration source,
and the [official configuration reference](https://developers.openai.com/codex/config-reference).
