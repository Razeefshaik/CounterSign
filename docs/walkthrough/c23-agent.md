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
call. With valid settings it makes exactly one model request, binding one harmless
read stub through the same LangChain adapter as the commander. It never calls
PayPal or invokes a returned tool. Output contains only provider, model ID,
whether a tool call returned, and token counts when available. There are no raw
responses, thought text or keys. A quota/provider failure is a fixed error; retrying
is an explicit owner action rather than an automatic loop.

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
