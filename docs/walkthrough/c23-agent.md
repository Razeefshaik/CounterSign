# C23: the Incident Commander

## 1. What this adds

The commander investigates an incident and proposes one next action. A proposal
contains the incident ID, action, parameters, evidence references, a reason and an
expected cash effect. Money uses integer minor units and a currency code.

The model can read through eight named tools. It cannot call the executor, import
the database layer or use the PayPal write adapter. The deterministic shield judges
its proposal before execution. The model has no authority to change a maker's payee.

## 2. How one step works

```mermaid
flowchart TD
    A[Incident and observed context] --> B[Separate trusted instructions from untrusted data]
    B --> C[Swappable model]
    C --> D[Read-only tool calls]
    D --> E[Bound and redact tool results]
    E --> C
    C --> F[Validate one structured proposal]
    F --> G[Shield evaluates authoritative records]
    G --> H[Block or request owner approval or allow]
    H --> I[Executor owns any later PayPal write]
```

The scripted model makes this flow testable without a network or an API key.
Selecting a real model requires explicit configuration; missing configuration
raises an error rather than returning a simulated recommendation.

`Engine.tick()` processes the inbox, reconciles, investigates one eligible
incident, then routes ready proposals to the executor and runs its recovery
sweep. Background worker intervals are unchanged. With `llm=None`, the commander
stage is absent. The tick's existing `proposals` count still means proposals
routed successfully to the executor; stored drafts and blocked proposals appear
in the snapshot rather than increasing that count.

The worker filters execution with the same `shield.snapshot.PAYING` set the
current executor checks. Unsupported proposals retain their `ALLOWED` or
`APPROVED` state and their approvals. Each receives one `EXECUTION_DEFERRED`
audit reason, including when workers overlap. Deferred items do not take queue
slots from supported payouts. Once that shared capability set changes, the
unchanged proposals become eligible for execution.

## 3. The eight tools

The five toolkit tools are `list_disputes`, `get_dispute`,
`get_shipment_tracking`, `get_order_details` and `list_transactions`. They come
from the pinned PayPal Agent Toolkit LangChain adapter. Startup checks the exact
set, including duplicates. It refuses additional tools and missing tools.

CounterSign adds three tools:

- `get_payout_item`: a GET through the existing PayPal reader.
- `query_money_graph`: named parameterized queries over masked dashboard views,
  in a read-only PostgreSQL transaction as `countersign_agent_ro`.
- `simulate`: an injected read-only simulator. Until C24 supplies a simulator,
  the default explicitly reports `SIMULATOR_UNAVAILABLE`.

The toolkit runs in a child process with a restricted environment. PayPal,
LangChain and LangSmith environment variables do not pass into it. Credentials
are passed explicitly through its private input channel; tracing is disabled.
The configuration enables only the five read tools. It never enables
`disputes.create`, which would grant `accept_dispute_claim`.

On Windows, subprocess pipes use background threads so the toolkit can share
psycopg's SelectorEventLoop. A timeout or cancellation kills and reaps the child;
the closed gateway cannot consume a stale response or silently replay a call.

## 4. Untrusted text and public records

Buyer notes, dispute messages and tool results are data. They are never inserted
as system instructions. Suspicious destination changes and instructions produce
flags such as `PROMPT_INJECTION` and `PAYEE_CHANGE_REQUEST`. Pattern detection is
an investigative hint, not a claim to detect every possible injection.

The fixed tool capabilities, strict proposal validation and deterministic shield
provide the execution boundary. Tool responses, model input and stored proposal
text are bounded and redacted. Errors contain fixed descriptions, never private
transport errors, database URLs or keys.

The pure `INJECTION_GUARD` rule blocks flagged financial proposals. The original
`PAYEE_INTEGRITY` rule independently blocks payees supplied in proposal params.
The injected refund test records both checks. Flags are loaded again from the
stored proposal before execution, so approving or retrying cannot erase them.
The rule is part of contract 1.0.2; `PROMPT_INJECTION` remains a flag type.

Investigation happens outside database transactions. Saving a proposal takes the
existing financial lock, checks the incident version and loads authoritative
records for the shield. Concurrent commanders cannot commit two proposals for
the same incident version. An unchanged handled incident does not consume another
model step. Evidence references supplied by the model remain claims, not proof.
Rejected or expired agent decisions resume investigation on the next tick,
including owner decisions made through the HTTP API.

## 5. Switching on a real model

The owner chose Google Gemini through AI Studio's free API tier. Model identity
is configurable, with `gemini-3.5-flash` as the configured default and
`gemini-3.5-flash-lite` as an explicitly selected eval fallback. Nothing contacts Google merely because a key
exists: the demo stays scripted unless `--model configured` is selected. A partial
configuration fails clearly; configured mode never falls back to a fake model.

### Why there are two Python environments

The optional `llm` extra pins the main process to:

| Package | Version |
| --- | --- |
| langchain | 1.4.7 |
| langchain-core | 1.7.0 |
| langchain-google-genai | 4.4.2 |
| google-genai | 2.33.0 |

PayPal's toolkit stays at 1.11.0 in the existing isolated child process, with
LangChain 0.3.23 and core 0.3.76. The parent selects that interpreter with
`COUNTERSIGN_TOOLKIT_PYTHON`; credentials still travel over the existing bounded
stdin protocol, and the child never inherits the Google key.

This is option (b) from C23b. LangChain documents that Gemini 3 needs Google
integration 3.1.0 or later and the original assistant messages returned with tool
responses. The 2.x integration does not meet that requirement. Google now limits
2.5 access to previously active users, so an older model is not a reliable setup
for a new project. The separate interpreter reuses the existing subprocess bridge
without mixing incompatible LangChain versions. Sources:
[LangChain thought signatures](https://docs.langchain.com/oss/python/integrations/chat/google_generative_ai#thought-signatures),
[Google model availability](https://ai.google.dev/gemini-api/docs/deprecations).

The adapter retains each original assistant message, including its opaque thought
signature, during the current bounded investigation. Tool results become proper
ToolMessages. It clears that private history on a final answer or error and
refuses a continuation from another incident. Signatures and raw responses are
never smoke-test output or public recordings.

### Owner setup, from CounterSign-db

1. Open [AI Studio's API key page](https://aistudio.google.com/apikey), select the
   project with free-tier quota, and create a key. Keep it private. Confirm quota
   and model access in that project; a Gemini consumer subscription is separate.
   Google's [API pricing](https://ai.google.dev/gemini-api/docs/pricing) currently
   lists free input/output for `gemini-3.5-flash`, the example below.
2. Install the optional main dependencies and the small adapter environment:

```powershell
$env:PYTHONPATH = 'src'
.venv\Scripts\python.exe -m pip install -e '.[dev,llm]'
.venv\Scripts\python.exe -m venv .venv\toolkit
.venv\toolkit\Scripts\python.exe -m pip install langchain==0.3.23 langchain-core==0.3.76 requests==2.32.5 python-dotenv==1.1.1 pydantic==2.12.5
.venv\toolkit\Scripts\python.exe -m pip install --no-deps paypal-agent-toolkit==1.11.0
```

The final `--no-deps` follows D13: this child uses only the genuine LangChain
adapter and its explicitly installed runtime, not the toolkit's unrelated
CrewAI/OpenAI agent integrations. Do not install both LangChain generations into
the same environment. The core install remains unchanged without the `llm` extra.

3. Set these names privately in the owner's runtime environment file:

```dotenv
COUNTERSIGN_LLM_PROVIDER=google_genai
COUNTERSIGN_LLM_MODEL=gemini-3.5-flash
GOOGLE_API_KEY=<paste the private AI Studio key here>
COUNTERSIGN_TOOLKIT_PYTHON=S:\StudyResource\TechBoooo\Hackathons\PayPalDevPost\CounterSign-db\.venv\toolkit\Scripts\python.exe
```

Configured Gemini uses `gemini-3.5-flash` when the model ID is omitted. Set
`COUNTERSIGN_LLM_MODEL=gemini-3.5-flash-lite` explicitly for the eval fallback;
there is no automatic retry on a different model. Other exact model IDs remain
configurable. The owner reported tool-call probes for Flash and Flash-Lite;
those access checks are not a real evaluation or proof of model quality.
Existing process environment values override an explicitly loaded file. Unset
settings return no model; a missing provider or key raises a fixed, value-free error.
The provider and key are required even when the default model is used.

4. Run the one-call smoke test yourself:

```powershell
$env:PYTHONPATH = 'src'
.venv\Scripts\python.exe -m scripts.llm_smoke --env-file ..\CounterSign\.env
```

Without `--env-file`, the command reads only the process environment, never an
implicit file. With no configuration/key it stops before a client or network
call. With valid settings it makes one logical model invocation, binding one harmless
read stub through the same LangChain adapter as the commander. It never calls
PayPal or invokes a returned tool. Output contains only provider, model ID,
whether a tool call returned, and token counts when available. There are no raw
responses, thought text or keys. The shared model client has up to three total
attempts with a 90-second timeout per attempt for transient provider failures.
A final failure is a fixed error; rerunning the smoke is an explicit owner action.

5. Once the owner smoke succeeds, follow the C25 receiver/tunnel/database runbook
   and add `--model configured` to its demo command. The default remains
   `--model scripted`. Configured runs use the genuine read toolkit and read-only
   graph role, and bind provider/model identity into the saved checkpoint. Old
   scripted checkpoint fingerprints remain compatible; switching model modes for
   an existing run is refused.

The real model makes its own proposals. The demo records what it actually chose
and stops if that differs from the staged week's expected action. It may correctly
refuse to propose an intentionally unsafe red-team action. Such a stop is not a
successful real-model week, and there is no scripted substitution. No real Google
or PayPal call was made while implementing this card.

### Hook Aman needs for M13/M16

`eval/runner.py::run_suite` already accepts `llm` and passes it to SystemContext,
but its CLI currently supports only the dummy system. M13/M16 modules are not yet
present. Aman should add the CLI `--model` selection, load the configuration once (explicitly choose Flash-Lite when requested),
and pass the same immutable configuration to both systems, with a fresh model
adapter per run. Report `config.model` as the result's actual model identity.
Neither the eval files nor their mock-only safety boundaries were changed here.

The existing facade export supplies configuration without a new Engine method:

```python
from countersign.harness import ChatModelConfig

config = ChatModelConfig.from_environment()  # None or validated configuration
# run_suite(..., llm=config, model=config.model) for explicitly configured runs
# M16: EngineSettings(..., llm=context.llm)
```

The C21 slice currently builds no commander/model and remains its deterministic
payout/reconciliation proof. The HTTP receiver also runs without a commander;
only the selected demo CLI performs model steps.

### C23c: rehearse the configured model against the local mock

This is a mock rehearsal, not a PayPal sandbox run. The owner already confirmed a
Gemini tool call with the smoke test. C23c's tests use a fake model and the genuine
isolated toolkit child; they make no real Google or PayPal requests.

Complete the two-environment install above first. Keep only the model provider,
model ID and Google key in the selected runtime file. A configured mock run copies
only `COUNTERSIGN_LLM_PROVIDER`, `COUNTERSIGN_LLM_MODEL` and `GOOGLE_API_KEY` from it,
with interpolation disabled and existing process values winning. PayPal credentials,
DATABASE_URL, owner settings and the toolkit interpreter in that file are ignored.
Scripted mock runs and the receiver do not read the file at all.

For a fresh rehearsal, stop the previous receiver and preserve its checkpoint.
Use a new empty database and a fresh scratch root. From `CounterSign-db`:

```powershell
docker compose --profile test up -d postgres-test
docker compose --profile test exec -T postgres-test createdb -U countersign countersign_rehearsal_c23c_demo
$env:PYTHONPATH = 'src'
$env:DATABASE_URL = 'postgresql://countersign:countersign-dev@127.0.0.1:55433/countersign_rehearsal_c23c_demo'
.venv\Scripts\python.exe -m alembic -c migrations/alembic.ini upgrade head
$rehearsalRoot = Join-Path $env:TEMP ('countersign-rehearsal-' + [DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss'))
.venv\Scripts\python.exe -m scripts.mock_rehearsal --reset --env-file ..\CounterSign\.env --root $rehearsalRoot
```

If that database already exists, select a new name ending in `_demo`; do not drop
working data. Port 55433 is the disposable test service and loses state on restart.
The `--reset` above is deliberate: it discards this local mock's previous state
for a fresh run. For resume, keep the original database, root, mock and webhook;
do not reset the mock, create another database or change the saved date.

The helper starts the mock on loopback port 8099 when absent and leaves it running
in the background. It uses fake mock OAuth credentials, registers or reuses the
wildcard webhook, and prints the actual public webhook ID plus two commands. It
never opens the runtime file or prints a database URL, model key or bearer token.
An existing mock is preserved when `--reset` is omitted. Keep the printed settings
and commands; the webhook ID is not hard-coded to an assumed seed sequence.

Run the printed shared environment settings in **both** PowerShell terminals,
including the same DATABASE_URL from above. They select `COUNTERSIGN_MODE=demo`,
`PAYPAL_BASE_URL=http://127.0.0.1:8099`, fake mock credentials,
`PAYPAL_WEBHOOK_ID` and the absolute `COUNTERSIGN_TOOLKIT_PYTHON` path. Then:

```powershell
# Terminal 1: the actual receiver, with background workers disabled
.venv\Scripts\python.exe -m scripts.demo_week --target mock --model configured --serve --port 8000 --env-file ..\CounterSign\.env

# Terminal 2: use the exact scratch root printed by the helper
.venv\Scripts\python.exe -m scripts.demo_week --target mock --model configured --env-file ..\CounterSign\.env --root 'PASTE-THE-PRINTED-SCRATCH-ROOT'
```

Only Terminal 2 invokes the real model. `adjudicate` remains the default. This is
an owner-operated Google call against a fully local PayPal mock; no sandbox
PayPal credentials are loaded. Keep this run separate from scripted acceptance
results. The model may choose WAIT or another action instead of the staged
expectation; `CONFIGURED_MODEL_DECISION_DIFFERS` records that actual choice and
stops honestly, without replacing it with DemoModel output.

The bridge reproduction established:

- `get_dispute` and the other reads contact the selected mock, not the sandbox.
  The guard still permits OAuth plus fixed GET paths only, with exactly five
  genuine toolkit tools. A changed SDK origin fails before any request.
- Old `DEMO-O-...` UUID strings did not match the SDK's 17-to-32 uppercase
  alphanumeric order-ID schema. Fresh mock orders/captures now use valid
  synthetic IDs. The mock already creates the corresponding order from each
  seeded capture; no new mock endpoint is required.
- SDK 1.11.0's tracking-by-order handler calls an absent `get_order_details`
  client method. Its transaction-ID search calls an unsupported `get(payload=...)`
  and expects JSON text. The child supplies narrow read-only compatibility
  helpers while keeping the genuine tool handlers and the existing HTTP guard.
- The transaction-ID search uses the SDK's real UTC month windows; a future
  accelerated mock clock can honestly produce `found=false`. Reporting lag can
  also produce empty lists. Neither is proof of a failed HTTP call or an absent
  transaction. The SDK order tool's canned ?captured? message is not evidence of
  capture: inspect its raw status and records.

Configured staging also uses fixed incident reason codes and identifiers; the
previous descriptive prose violated IncidentService's subject validation and
blocked later model steps. Human-readable staging is logged separately.

All five tool names, tracking by transaction and by order, transaction-ID search,
argument validation, mock routing and the financial-write guard have offline
coverage. A database integration test uses a fake LangChain reply through the
normal configured Engine and restricted graph/toolkit startup, reads the order
and dispute, and executes one allowed evidence submission against the local mock. These transport checks do not claim that a real-model week completed.
Saved runs with the old invalid synthetic IDs are not silently rewritten; start
fresh as above when validating the corrected setup.

## 6. Scope and remaining work

C23 supplies investigation, read tools, one proposal per step and the connection
to the shield. Claude's C19b supplies execution of cancel, evidence and claim
actions. C24 supplies the full simulator and remaining incident lifecycle rules.
The HTTP dashboard remains on fixtures until M15.

Waiting records `WAITING_EXTERNAL`; requesting help records `AWAITING_OWNER`;
drafting only stores a draft. Local actions create no PayPal operation and never
claim a message was delivered or an incident was resolved. Financial actions
remain shield-judged proposals for the owner or executor. The integration test
creates its normal dispute incident explicitly because normal lifecycle creation
is C24 work.

Mate's schemas accept arbitrary string rule names. Its `web/fixtures/meta.json`
still advertises contract 1.0.1; mate should update that fixture to 1.0.2. This card
changes only the approved contract header, rule list and meta example.

Actual model quality and cost remain unmeasured until the owner authorizes a
real evaluation with the selected model. Offline scripted tests validate the boundaries
and orchestration; they do not claim that a real model completed the task.
