# CounterSign: Decisions Log

Project was called OnceGuard Money Ops until Oct 23, 2026; older rows keep the old name.

Put this in the repo as `docs/DECISIONS.md`. Every review suggestion from Oct 23, 2026 is listed with the decision and a one line reason. Sources: GPT review, Gemini review, and the planning chat (Claude). "Verified" means checked against official PayPal documentation, not yet tested in our sandbox.

## Verified API facts introduced by reviewers

| Fact | Source | Status |
| --- | --- | --- |
| Balances API returns total, available and withheld balances with a last refresh time; can lag up to 3 hours | GPT | Verified |
| Payout item webhooks include HELD, SUCCEEDED, RETURNED, CANCELED, BLOCKED, FAILED, REFUNDED, UNCLAIMED, DENIED; batch webhooks carry no item info | GPT | Verified |
| Webhook HELD vs item status ONHOLD; webhook SUCCEEDED vs item status SUCCESS | GPT | Verified |
| Agent Toolkit dispute tools are list_disputes, get_dispute, accept_dispute_claim; tracking is get_shipment_tracking; no payout or provide evidence tools | GPT (names), Claude (no payout or evidence tools) | Verified |
| Transaction IDs are not unique in PayPal reporting | GPT | Verified |
| Unclaimed payout money is not part of available balance | Both | Consistent with PayPal docs; confirm in sandbox |
| Sandbox webhooks lag or drop; sandbox balance may not reflect dispute holds promptly | Gemini | Unverified opinion; designed around it anyway (poller, resource state reconciliation) |

## Story and positioning

| Suggestion | Source | Decision | Reason |
| --- | --- | --- | --- |
| Fix demo arithmetic ($1,500 before or after the $300 payout was ambiguous) | Both | Accept | A math error in a money product destroys judge trust |
| Narrow persona | Both | Accept | Vague persona weakens Potential Impact |
| Persona: 3 person agency or micro marketplace | Gemini | Accept the micro marketplace variant | Payouts to sellers is a textbook Payouts use |
| Persona: small physical goods seller | GPT | Partly accept | Kept physical goods (tracking evidence) inside the marketplace persona |
| Old story had little economic interaction between decisions | GPT | Accept, fixed by persona change | In a marketplace, a dispute changes who gets paid, not just cash level |
| Rename "payroll" to "payout run" | Both | Accept | Payroll implies employees and tax |
| Replace absolute safety claims with named invariants and test results | GPT | Accept | Absolute claims are indefensible |
| Do not claim "nothing shows it" | GPT | Accept | PayPal has a Resolution Center; claim the missing connection instead |
| Cite "disputes cost 2x to 3x" stat | Gemini | Reject unless a primary source is found | Unsourced numbers are a liability with PayPal judges |
| Three user interviews | GPT | Defer | Hard to reach PayPal Payouts merchants in this window; use public community threads instead |
| "Already qualifies for Best Use of Agentic Commerce" | Gemini | Reject as a pitch point | Every eligible project qualifies; not a differentiator |

## Architecture and scope

| Suggestion | Source | Decision | Reason |
| --- | --- | --- | --- |
| Cut FalkorDB; money graph in PostgreSQL | Both | Accept | Tiny data; two stores can disagree about money |
| Cut separate queue; PostgreSQL transactional inbox | Gemini | Accept | Exactly once processing in the same transaction as ledger writes |
| Cut intent receipts (Layer 2) | Both | Accept | Different user; splits the product |
| Cut Invoicing and Subscriptions | Both | Accept | Setup cost, no proof of the core idea |
| Cut dispute offers | GPT (cut), Gemini (keep, owner gated) | Move to stretch goal A with a real buyer side | Fragile in sandbox now; worth doing properly if time allows |
| Cut appeals and conversational negotiation | GPT | Accept | Not central |
| Cash connection must be in the first working flow | GPT | Accept | It is the differentiator; must not slip to the end |
| Start build now, not after Oct 26 | Claude | Accept | Submission period is open; day one checks are needed regardless |
| Smarter shortfall prioritisation | Claude | Stretch goal B | Real treasury reasoning; only after the core is done |
| Autonomy tiers: no money out = autonomous; money out or refund = owner | Gemini (tiers), GPT (owner authorises money actions, executor retries automatically) | Accept, merged | Shows real agency without autonomous payouts or refunds |
| Make the AI's role concrete (evidence strength, injection flagging, plain English briefs) | Gemini | Accept | Answers "why not just if else" |

## Safety and correctness

| Suggestion | Source | Decision | Reason |
| --- | --- | --- | --- |
| "One live payout per obligation" allows paying again after success; add terminal SETTLED state | GPT | Accept | Closes the biggest duplicate payment hole |
| Write ahead operation record; UNKNOWN state resolved by querying PayPal | GPT | Accept | Crash after PayPal accepts must never cause a second payout |
| Re-check all rules at execution inside a locked transaction | Both | Accept | Prevents stale approvals and time of check vs time of use bugs |
| Approvals bound to payload hash, record versions, expiry, single use | GPT | Accept | Stale or replayed approvals |
| Reserve cash for pending proposals and approved runs | Both | Accept | Two plans must not spend the same money |
| One payout item per attempt; keys from obligation id and attempt number | Gemini | Accept | Partial batch failure cannot double pay |
| Reconcile each item; never mark obligations paid from batch events | GPT | Accept | Batch webhooks have no item info |
| Verify webhook signature and confirm state by GET before unlocking a replacement | Both | Accept | Forged or stale cancel events |
| Use cancellation outcome plus refreshed item state (claim during cancel race) | GPT | Accept | A previous UNCLAIMED read is not enough |
| ACCEPT_CLAIM always needs the owner | Gemini | Accept | Prevents sub threshold injected refunds |
| Aggregate refund limits per order and per day | GPT | Accept | Many small harmful refunds |
| Evidence must match merchant, transaction, order and amount; dispute status and action link checked | GPT | Accept | Real evidence on the wrong case |
| Reconcile against resource states, not only the balance API | Gemini | Accept | Balance lags; resource states are authoritative |
| Balance API shown with freshness; never subtract holds twice | GPT | Accept | Available already excludes withheld money |
| Fail closed only for the affected obligation or dispute | GPT | Accept | Global blocking would miss dispute deadlines |
| DEFER only payouts not yet sent; owner top up is an external task confirmed by observed funds | GPT | Accept | No API to delay sent payouts or fund the account |
| Restrict read tools; no free SQL, URLs or credentials | GPT | Accept | Read tool injection and leakage |
| Keep sandbox test controls out of agent tools | GPT | Accept | Agent must not settle its own disputes |
| Do not deduplicate imports on transaction id alone | GPT | Accept | Transaction IDs are not unique in reporting |
| Polling fallback for webhooks | Gemini | Accept | Sandbox delivery is unreliable |

## Evaluation and presentation

| Suggestion | Source | Decision | Reason |
| --- | --- | --- | --- |
| Run the big fault benchmark on a local mock from recorded payloads; live sandbox for the demo | Gemini | Accept | Rate limits and webhook lag make hundreds of live runs impractical |
| Report mock and sandbox results separately | GPT | Accept | Honesty |
| Fair baseline with normal idempotency handling | GPT | Accept | Otherwise we only prove careful beats careless |
| Rules only baseline for unstructured tasks | GPT | Accept | Shows what the AI adds |
| Report task completion next to safety | GPT | Accept | A system that blocks everything is safe and useless |
| Naive vs OnceGuard replay in the UI | Gemini | Accept, using the fair baseline | Strong visual for Presentation |
| AG Grid incident table and demo week control bar | Gemini | Accept | Covers frontend weakness; sponsor prize; judges never see an empty dashboard |
| Video target 2:45 with on screen labels for staged actions | GPT | Accept | Margin under the 3 minute limit; honesty about sandbox staging |
| Hook with a visible failure in the first 15 to 20 seconds | Gemini | Accept | Stronger opening |
| Show a schema valid proposal paying an already settled obligation being blocked | GPT | Accept | A realistic failure, not a strawman |

## Scores given by reviewers (for reference)

* Gemini: 39 of 50 (Tech 8.5, Design 6.5, Impact 8, Innovation 8.5, Presentation 7.5)
* GPT: 30 of 50 (Tech 7, Design 5, Impact 6, Innovation 6, Presentation 6)
* Plan against the stricter score; both scored the pre review design.

## Oct 23 owner decisions after the plan review (docs/PLAN.md)

| Change | Source | Decision | Reason |
| --- | --- | --- | --- |
| Rename OnceGuard Money Ops to CounterSign | Owner | Accept | OnceGuard is the separate Airwallex project; CounterSign names this project's core principle: the AI proposes, the code countersigns |
| F1: UNKNOWN resolved only by identical replay (same keys and body) within 25 days, then owner | Plan review | Accept | PayPal has no lookup by sender_batch_id and the create response has no item ids; keys last 30 days |
| F2: cancel before replace requires GET showing RETURNED | Plan review | Accept | CANCELED is a webhook name, not an item transaction_status |
| F5: open sandbox disputes as the buyer in the Resolution Center, labelled staged | Plan review | Accept | API creation needs a DISPUTE_CREATE scope only a PayPal account manager can add |
| F6: pain 1 reworded to "may freeze"; cash reads actual withheld amount | Plan review | Accept | PayPal documents no-hold disputes, and chargebacks are reversals |
| F4: "available excludes unclaimed money" moved to verify (D4) | Plan review | Accept | Not stated in PayPal docs |
| F12: explicit funding formula (snapshot minus outflows after last_refresh_time) | Plan review | Accept | Balance lags up to 3 hours; avoids subtracting payouts twice or not at all |
| F15 + payout lines: keys from payout line id (UUIDv7) and attempt number | Plan review | Accept | One payout item pays many obligations; UUIDs prevent key reuse after a database reset |
| F16: DISPUTE_LOST_PARTIAL with an ADJUSTMENT obligation | Plan review | Accept | Disputes can end in partial refunds |
| F18: evaluation baseline runs only on the mock, with documented idempotency, never sandbox credentials | Owner | Accept | Keeps the hard rule that no agent calls money moving PayPal APIs, while staying a fair baseline |
| F10, F13, F11, F17: tracking is merchant uploaded; unsupported states synthesised and labelled; test-only webhook signer; accept claim funding verified | Plan review | Accept | Honest evidence claims and evaluation |
| Agent tools via the toolkit's LangChain adapter behind an exact allowlist; model swappable, chosen at core step 5 | Owner | Accept | Python toolkit has no Anthropic module; a cheap model can run the large evaluation |
| Vertical slice first; DB transition triggers, allowed_transitions and property tests deferred until it works | Owner | Accept | Get one sandbox flow working end to end by Nov 5 before hardening |
| Day one checks timeboxed to Oct 25; BLOCKED is recorded, not chased | Owner | Accept | Protect build time |
| Work split across core and mate with folder ownership (AGENTS.md); interfaces fixed in docs/CONTRACTS.md | Owner | Accept | Three parallel workers need stable boundaries |
| Dashboard data comes from core-owned `v_dash_*` views with a `doc jsonb` column shaped exactly as the API item | Plan | Accept | Mate serves the API without importing core internals; fixtures first, views later |
| Core exposes `countersign.harness.Engine` as the only module mate code imports | Plan | Accept | Eval and the demo bar need one stable entry point |
| Demo control bar endpoints live in mate's `api/dashboard.py` and reuse the eval runner | Plan | Accept | The demo week is just an eval scenario played step by step |
| Eval invariants computed from `mock_paypal` ground truth, not from system traces | Plan | Accept | A system cannot grade itself |
| Mate also owns tests/mock_paypal/, tests/eval/ and tests/dashboard/ | Owner | Accept | Mate's tests live next to its ownership boundary, outside core's tests/ |
| Demo mode shows a persistent "Simulated: mock PayPal" banner whenever the demo bar replays the week (CONTRACTS 1.0.1) | Owner | Accept | Nobody may mistake simulated activity for live sandbox activity |
| D7 probes ERRPYO015 on both the batch and the item GET path | Day one harness | Accept | PayPal's test-values page lists 015 under the batch call while PLAN and the mock use the item call; the recording settles it |
| D13 installs paypal-agent-toolkit 1.11.0 with --no-deps plus langchain, requests and pydantic | Day one harness | Accept | The full install pulls crewai-tools, which does not build on Windows with Python 3.12; the LangChain adapter needs only these |
| The Agent Toolkit is always built with Context(sandbox=True), and the base URL is checked before any call | Day one harness | Accept | Context() defaults to the live PayPal API |
| Toolkit processes run without PAYPAL_, LANGCHAIN_ or LANGSMITH_ variables and with tracing off | Day one harness | Accept | LangChain could otherwise upload tool traces holding PayPal data to LangSmith; C23's agent must do the same |
| Money shares round half-up to the nearest cent; allocate() splits by largest remainder so parts always sum exactly | C10 | Accept | demo_week needs 90% of $333.33 to be $300.00; no cent is ever lost or invented |
| First PayPal read of a payout attempt is expected for any status a new item reaches itself; RETURNED, REFUNDED or REVERSED on first sight open an incident | C10 | Accept | Those statuses need history we never observed (cancel, 30 days, clawback; REVERSED is web-upload only, F3) |
| Dispute has no transition machine: phase() derives it from mirrored PayPal fields and fails closed to UNKNOWN_OUTCOME (reserve kept, incident opened) | C10 | Accept | PayPal owns dispute status (F8); OUTCOME_MAP stays empty until D10 records real outcome codes |
| Incident moves filled in where PLAN says "...": RESOLVED only via RECONCILED from INVESTIGATING or WAITING_EXTERNAL; a successful execution waits for external confirmation | C10 | Accept | An incident never closes mid-write or before the reconciler agrees with PayPal |
| Migration 0003 adds version to operations, payout_attempts and proposals, and state CHECKs to proposals and incidents; a unit test keeps every CHECK list equal to the C10 enums | C11 | Accept | Compare-and-set must guard the rows where a lost race costs the most; PLAN 2.2 omitted these |
| Local PostgreSQL publishes on 127.0.0.1 with a POSTGRES_PORT override (55432 on the owner's machine) | C11 | Accept | Windows reserved port 5432 |
| Ledger journals post once per (operation, event): a transaction-scoped advisory lock plus lookup, with the event in the memo prefix; READ COMMITTED only | C16 | Accept | PLAN 2.2 has no event column or unique key on ledger_journals; a retry must never post twice |
| One active dispute earmark per (dispute, obligation), enforced by an advisory lock and a check; the obligation must belong to the disputed order | C16 | Accept | No unique index in PLAN 2.2; a double earmark would hide cash that is really free |
| UNCLAIMED payouts move in_transit to paypal:unclaimed; RETURNED moves back from wherever the money sits [pending D4] | C16 | Accept | HANDOFF 10 assumes available already excludes unclaimed money; D4 confirms or changes it |
| refund() requires the caller to name the source (withheld or available); no default [pending D10] | C16 | Accept | F17: whether a held dispute refunds from withheld or available money is unverified |
| A FAILED payout after submission gets its own journal (in_transit back to available) | C16 | Accept | Otherwise money stuck in transit would never return in the books |
| Shield rule results carry rule, outcome and a string detail (CONTRACTS shape); structured facts go to the audit log; rules that do not apply are listed as not_applicable | C17 | Accept | CONTRACTS 2.3 examples list only applicable rules and the dashboard schema allows no extra fields |
| APPROVAL_BINDING returns NEEDS_OWNER for owner-tier actions before execution and PASS/BLOCK at execution | C17 | Accept | None of the ten slice rules owned the HANDOFF section 6 tier table |
| FUNDING never answers NEEDS_OWNER in the slice; an identical replay needs no new cash; the proposal's own RUN_CASH or PROPOSAL_CASH is not counted twice | C17 | Accept | HANDOFF names no case for NEEDS_OWNER; keeps the rule a plain yes/no on unreserved cash |
| Approvals bind maker, line and obligation versions plus direct attempt or dispute targets only (shield record_versions()) | C17 | Accept | Every sync bumps attempt and dispute versions; binding all of them would make every approval stale |
| C19 must route UNKNOWN attempts past the 25-day replay window to the owner as their own question, not through a stored tier approval | C17 | Accept | At execution PLAN turns NEEDS_OWNER into ALLOW with any valid approval, which may not cover this case |
| Import-linter forbids the shield from importing db, paypal, executor or agent | C17 | Accept | Rules must stay pure functions over a snapshot |
| A run executes as one operation per line, all saved, shield-checked and approval-consumed in one locked transaction; WRITE_AHEAD and APPROVAL_BINDING compare the run payload rebuilt from the stored operation rows | C19 | Accept | Each PayPal item needs its own write-ahead row, while the owner approves the run once; the rebuild proves the stored rows are exactly what was approved |
| A run approval is single use: used_at plus used_by_operation_id set to the run's first operation, under a used_at IS NULL guard | C19 | Accept | approvals.used_by_operation_id holds one id; every operation also carries approval_id |
| A shield refusal at execution blocks the operations, voids the approval unused, releases the run's cash and returns its obligations to OPEN | C19 | Accept | Otherwise obligations stay SCHEDULED with no path forward, and nothing was sent |
| Any adapter exception counts as UNKNOWN; a replay never ends in FAILED, it only confirms or stays UNKNOWN | C19 | Accept | We cannot prove a request was not sent, so only an identical replay or a matching original batch settles it |
| An IN_FLIGHT payout with no recorded answer after 10 minutes becomes UNKNOWN and goes to replay | C19 | Accept | Covers an executor that died between the call and its record; identical keys make the replay safe |
| An UNKNOWN past 25 days opens one PAYOUT_ITEM incident AWAITING_OWNER and sends nothing | C19 | Accept | Implements the C17 handoff; PayPal's key memory is only 30 days |
| The C19 executor handles paying actions only; cancel, evidence and accept-claim execution move to a follow-up card before C21 | C19 | Accept | Their approved payload has no persisted request body yet; keeps the slice's money path reviewed first |
| Run planning nets OPEN positive shares and negative F16 adjustments per maker; nonpositive totals stay OPEN | C15, owner approved | Accept | Ignoring a partial-loss adjustment would overpay the maker when the original share reopens |
| Attempt polling also revisits terminal PayPal states; durable journal history prevents re-settlement after a status regression | C15 | Accept | SUCCESS can later claw back, and a surprising GET must be retained without paying twice |
| UNCLAIMED success credits the unclaimed account; settled clawbacks credit maker payable; require C19's submitted journal first | C15 | Accept | Each confirmed movement must use the account where the existing balanced journals put the money |
| Clawbacks reopen obligations and release old allocations but keep PAID lines with a reconciliation incident | C15 | Accept | C10 has no legal PAID-to-UNPAID line event; reconciliation must not invent a transition |
| A historical attempt that conflicts with the live-attempt index keeps the observation in paypal_raw and opens an incident | C15 | Accept | Preserve unexpected GET evidence without weakening the one-live-attempt safety constraint |
| Balance imports preserve NULL amounts, validate all currencies before writes, and skip identical or older refreshes | C15 | Accept | An absent or stale observation must not become spendable cash or overwrite a newer one |
| Event-history backfill defaults off and uses private policy checkpoints after fresh resource GETs; never invent signed inbox deliveries | C15 | Accept | D11 is pending and event history does not contain original delivery bytes or signature headers |
| An observed unjournaled refund makes available cash unknown until both balance timestamps cover its first observation | C15 | Accept | D10 has not verified the refund source, so neither a guessed cash journal nor stale pre-refund funding is safe |
| Dispute polling discovers missing opening notifications with list plus fresh GET; only seller_transaction_id links a capture | C15 | Accept | Dropped webhooks must not leave a known maker share payable, and D8 confirmed the identity field |
| Dispute financial resolutions use an append-only STATE_TRANSITION audit marker; partial losses add a negative adjustment with the original due date | C15 | Accept | Retries cannot duplicate a refund adjustment or postpone its deduction from the original payable share |
| Conflicting adjustments, changed resolutions, partial-order disputes and paid-before-dispute losses retain protection for investigation | C15 | Accept | The current schema and pending outcome facts do not justify guessing a release or overwriting prior financial history |
| Reconciliation tries the owner-decision lock for one READ COMMITTED cycle with per-resource savepoints; scheduled default is 60 seconds | C15 | Accept | Racing cycles do no duplicate work, failed resources remain retryable, and crashes cannot commit half a financial update |
| C15 exposes a standalone scheduler and poll_once interface; C20 wires it into the application facade | C15 | Accept | Main and worker integration is outside this card and Claude is implementing the executor in parallel |
| Refund freshness thresholds and balance fetched_at use post-GET observation times, not poll-cycle start | C15 review | Accept | A balance refreshed during a cycle but before a later refund must not clear the unknown-cash guard |
| Tests of completed poll work wait for a non-busy cycle; the concurrency assertion still checks immediate busy | C15 validation | Accept | Isolated schemas in parallel worktrees share PostgreSQL's database-wide advisory lock |
| The engine facade constructs execution through Executor.from_http and calls only execute, sweep and status | C20, C19 factory | Accept | The executor remains the only package importing the PayPal write adapter, including while C19b is built separately |
| Engine resources open lazily and expose async context management and aclose; injected resources remain caller-owned | C20 | Accept | Eval and application wiring can share connections without imports opening sockets or shutdown closing someone else's pool |
| Reset checks both configured and connected database names for _test or _eval, then truncates fixed schema-qualified tables without CASCADE | C20 | Accept | A deceptive URL suffix or an injected production connection cannot authorize destructive reset |
| Historical payouts require explicit order_keys when their maker has current shares; an empty list explicitly means separate history | C20, owner approved | Accept | Equal maker and amount cannot distinguish a prior week's payout from a payout that already settled the listed order |
| Historical payouts are confirmed by item and batch GETs, imported without approvals or fabricated proposals, and use actual observed sender keys and creation time | C20 | Accept | Bootstrap must preserve facts without claiming the original system executed a CounterSign proposal |
| A historical request ID unavailable from GET gets a labeled non-replayable import marker in a SUCCEEDED operation | C20 | Accept | PayPal does not expose the original request header, so deriving a plausible key would invent replay metadata |
| Engine snapshots read masked views under the financial lock and return cash null when the latest observation is incomplete or refund-guarded | C20 | Accept | An older complete dashboard view must not hide currently unknown spendable cash |
| Worker loops default to inbox 5 seconds, reconciliation 60 seconds and executor maintenance 60 seconds; intervals load at startup | C20 | Accept | Independent bounded loops keep recovery scheduled while avoiding overlapping invocations within each stage |
| Facade ticks report completed webhook deliveries, routed proposals, new operation IDs and safe error counts; unsupported actions remain executor-owned | C20 | Accept | UNKNOWN verification and identical replays must not be presented as newly completed deliveries or new money operations |
| Non-null LLM settings fail explicitly until C23 rather than simulating an agent pass | C20 | Accept | The facade must report the current implementation honestly while remaining a stable entry point for later agent wiring |
| C20 reuses mate schemas with local corrections for nullable incident/reservation links, arbitrary version-map keys and state-dependent approval_request | C20 validation | Accept | Fixture examples overfit these fields; source records remain truthful and mate-owned schema files are not edited |
| C23 keeps one strict proposal per bounded model step, with a scripted model and an optional explicit LangChain configuration | C23 | Accept | The provider is not chosen yet; offline boundary tests must not claim real model reasoning or make network calls |
| C23 custom reads use fixed dashboard-view queries under countersign_agent_ro, and simulate reports unavailable until C24 is injected | C23 | Accept | A model must never supply SQL or receive invented financial predictions |
| INJECTION_GUARD independently blocks every financial proposal carrying PROMPT_INJECTION, including fresh execution-time snapshots; harmless waiting, drafts and owner requests remain available | C23, owner approved | Accept | A flagged instruction must not authorize money movement even when its params name no new payee |
| CONTRACTS 1.0.2 adds INJECTION_GUARD to the rule-name list and updates the meta example; PROMPT_INJECTION remains only a flag type | C23, owner approved | Accept | Flags describe observed text while rule results name the deterministic check that refused it |
| C23 toolkit processes inherit only OS essentials, use stdin-only explicit credentials and disable tracing plus callbacks behind a same-origin OAuth-and-GET transport guard | C23 | Accept | Tool names alone cannot constrain redirects, adapter mistakes or inherited trace settings |
| C23 runs one commander step between reconciliation and existing execution/recovery without changing worker intervals; local waits, stored drafts and owner questions use no PayPal operation | C23, owner approved | Accept | The facade must record what happened without claiming a draft was delivered or an incident reconciled |
| Commander results commit only after a fresh shield check under the financial lock and an incident-version check, with one handled proposal per version | C23 | Accept | Slow or concurrent model calls must not duplicate proposals or overwrite newer observations |
| Windows toolkit IPC uses bounded thread-backed pipes while other platforms use asyncio subprocesses | C23 validation | Accept | psycopg requires the Windows SelectorEventLoop, which cannot create native asyncio subprocess transports |
| Rejected or expired agent decisions resume AWAITING_OWNER incidents on the next facade tick through the existing FSM | C23 review | Accept | Decisions made through HTTP or just before a crash must not strand an incident outside the commander's eligible queue |
| Worker dispatch filters by shield.snapshot.PAYING and records EXECUTION_DEFERRED once per unsupported proposal without changing state or approvals | C23, owner approved | Accept | Unsupported ready actions must not fill the execution limit or require rewriting once the executor gains their capability |
| D6 DIFFERENT: every duplicate returns 400 USER_BUSINESS_ERROR with the original batch in details[].link (host api.sandbox.paypal.com); UNKNOWN is resolved by following that link, never by expecting a 2xx replay | C19 | Accept | The sandbox duplicate response does not report the current batch state |
| Non-paying requests bind action, params, trusted internal and PayPal targets, stable request ID, exact body and body format in canonical JSON SHA-256; dispute observations and refund amount are bound too | C19 | Accept | Rebuilding locked records before execution prevents changed tracking, targets or serialization from escaping approval |
| Evidence uses stored tracking only and requires WAITING_FOR_SELLER_RESPONSE plus provide_evidence; multipart input JSON and accept-claim JSON are configurable pending D10 | C19 | Accept | Status and available action links determine eligibility without guessing a lifecycle stage or switching formats after an uncertain write |
| Cancel requires a matching RETURNED item GET; uncertain cancel, evidence and accept-claim are GET-only and open one AWAITING_OWNER incident when undecidable | C19 | Accept | Non-idempotent POSTs must never be repeated to discover whether the first write happened |
| ACCEPT_CLAIM checks explicit PAYPAL_REFUND_SOURCE before Transaction A and defers with one REFUND_SOURCE_UNCONFIGURED audit while leaving proposal state and approval untouched; Claude sets it from D10 balance observations | C19, owner approved | Accept | Available versus withheld remains unverified; freezing the configured source separately in write-ahead metadata preserves the same HTTP approval and safe recovery |
| An available-funded accept-claim reserves PROPOSAL_CASH in Transaction A, keeps it through UNKNOWN, and consumes it atomically with the confirmed refund journal or releases it on a definite rejection | C19 | Accept | An uncertain refund must not leave the same available cash spendable by an unrelated payout |
| Worker and executor use shield.snapshot.EXECUTABLE for payout create, cancel, evidence and accept-claim while PAYING retains only maker payouts | C19 | Accept | Previously deferred actions can run unchanged without broadening the funding rules to non-payout actions |

## Oct 24 C24 rules, incidents and simulator

| Change | Source | Decision | Reason |
| --- | --- | --- | --- |
| C24 evidence autonomy requires WAITING_FOR_SELLER_RESPONSE plus the provide_evidence link, with no stage gate | C24, owner prompt | Accept | D8 allowed chargeback evidence while D9 lacked the link; the action link decides availability |
| C24 reuses refund_limit_per_order and refund_limit_per_day money policies and counts committed or ambiguous full claims plus observed refunds | C24 | Accept | ACCEPT_CLAIM always refunds the claim and small proposed amounts must not bypass aggregate limits |
| C24 deadline_close_seconds is a required positive integer policy; missing policy needs the owner, missing deadline or action link blocks | C24, owner approved | Accept | No invented deadline or implicit threshold may authorize an action |
| C24 adds only trailing optional snapshot facts and enriches load_context through load_rule_facts without changing payloads or hashes | C24, owner approved | Accept | Keeps the parallel C19b executor and canonical payload changes isolated |
| C24 evidence accepts stored order/capture and tracking references, rejects free text, and identifies unvalidated merchant tracking explicitly | C24 | Accept | F10 prohibits claiming merchant-uploaded status is carrier-confirmed |
| C24 lifecycle composes the existing reconciler in harness wiring and requires fresh GET mirrors plus matching financial records before closure | C24 | Accept | The poller and executor stay in their ownership boundaries; successful writes alone cannot resolve an incident |
| C24 preserves executor-created AWAITING_OWNER incidents and adds lifecycle audits without rewriting their subjects | C24, owner prompt | Accept | UNKNOWN past the replay window is an owner question already opened by C19 |
| C24 simulator uses a repeatable-read read-only transaction, replaces the run's own cash commitment once, and changes payable shares rather than cash earmarks in its counterfactual | C24 | Accept | The PLAN formula must not subtract holds or commitments twice, and simulation must never mutate money |
| C24 resolved resource changes may open a new incident; unchanged GET observations do not repeat model steps or mismatch audits | C24 | Accept | Recovery needs new reasoning when facts change without creating a polling loop of duplicate work |
| C24 evidence uses the stored carrier and number/tracking_number dictionary and rejects conflicting numbers or a mismatched tracking transaction | C24 | Accept | The shield and C19b must judge the same stored tracking identity, never select between conflicting facts |
| C24 accepts the last confirmed terminal dispute resolution only when its financial marker agrees and no later or unresolved dispute write exists | C24 integration | Accept | C15 retires resolved disputes after their earmarks close, so requiring a new GET every tick would strand delayed reviews and C19b confirmations |
| C24 core facade and demo script seed deadline_close_seconds = 172800 (48 hours), with explicit policy overrides preserved | Owner | Accept | owner can change it; the shield still fails closed when the policy row is missing |
| Contract 1.0.3: the mock answers every duplicate payout POST (same or new PayPal-Request-Id, changed body, concurrent loser) with 400 USER_BUSINESS_ERROR and the original batch in details[].link; no 201 replay and no 409 | Owner (D6) | Accept | D6a to D6d recorded exactly this on the sandbox, and the mock must not be kinder than PayPal |
| The mock duplicate link uses its own base URL by default; duplicate_link_host="sandbox" reproduces the recorded api.sandbox.paypal.com host | Owner (D6) | Accept | core follows links only to its configured host or the one sandbox alias, so the default keeps local recovery testable |
| M09a fixes the confirmed D5 cancel response to RETURNED and accepts JSON or multipart input for both dispute writes pending D10 | Owner prompt | Accept | Cancellation must match the observed response, while local dispute tests must support both formats until the sandbox format is confirmed |

## Oct 25 C22 deferred hardening

| Change | Source | Decision | Reason |
| --- | --- | --- | --- |
| C22 generates unique state pairs from the six Python FSM tables into a frozen 0004 JSON snapshot; unit and database tests reject drift | C22 | Accept | Historical migrations remain reproducible while a changed FSM cannot silently diverge from the database |
| C22 checks attempt transitions only when OLD.state is PREPARED or UNKNOWN; same-state updates and later PayPal mirrors pass | C22, owner prompt | Accept | Unexpected GET-confirmed statuses must be retained and investigated rather than rejected by the database |
| C22 resolves allowed_transitions through TG_TABLE_SCHEMA in the shared trigger function | C22 | Accept | A caller search path must not select a different transition table |
| C22 repairs test-only state shortcuts: cancel fixtures schedule before flight, unapproved claims start from CREATED, and reconciliation fault injection changes an amount rather than violating SETTLED | C22 validation | Accept | The database guard stays enabled and no production transition is added for a fixture |
| C22 executor-race examples each use a fresh migrated schema and never call Engine.reset on the shared fixture | C22 review | Accept | Property-test isolation must also work with the standard countersign database name without weakening the reset guard |
| C22 executor races await observed settlement through bounded ticks rather than assuming one error-free tick made progress | C22 standard-environment validation | Accept | The reconciliation advisory lock spans schemas and a busy cycle legitimately returns without polling |

## Oct 25 C21 vertical slice

| Change | Source | Decision | Reason |
| --- | --- | --- | --- |
| C21 keeps fault injection in a script-only transport and uses fsynced atomic checkpoints plus a per-run file lock; sandbox order setup reuses the bound harness writes | C21 | Accept | The demo must survive interruption without changing production HTTP behavior, replaying completed writes or exposing credentials |
| C21 closes the payout incident while Maker A's dispute and reserved share remain active; a script-only replay pause lets the real lifecycle observe UNKNOWN before recovery | C21, owner approved | Accept | Releasing A's reserve would contradict the slice, and an immediate same-tick recovery would leave no pending payout incident to demonstrate |
| C21 receiver runs the existing app without background workers while only the script ticks its dedicated empty database | C21 | Accept | Another executor process would bypass the staged transport and could send the payout before the intended lost-response fault |
| C21 may read webhook_inbox by exact event_type and run resource_id, requiring signature SUCCESS and status PROCESSED, with no inbox writes or facade/contract changes | C21, owner approved | Accept | Public snapshots cannot prove which delivery was verified and processed; an unrelated processed event must never satisfy the slice |
| Inbox marks a signature-verified, GET-confirmed payout event PROCESSED; it still moves no payout state (C15 owns posting). | C21, owner approved | Accept | The slice needs exact processing evidence while payout reconciliation and ledger posting remain with C15 |
| C21 tests M09a's actual duplicate 400 and original-batch link with identical saved keys and payload, with C22 migration 0004 guards enabled and no synthetic duplicate bridge | C21 integration | Accept | Recovery must prove the current mock and guarded database work together rather than substitute the behavior being tested |

## Oct 25 local test infrastructure

| Change | Source | Decision | Reason |
| --- | --- | --- | --- |
| C20 adds an opt-in postgres-test service on localhost:55433 with tmpfs data and fsync, synchronous_commit and full_page_writes disabled; the existing postgres service and volume are unchanged | Owner test-infrastructure prompt | Accept | Disposable test databases can trade crash durability for faster Windows test runs; sandbox working state must retain crash-safe storage |

## Oct 25 C25 demo week

| Change | Source | Decision | Reason |
| --- | --- | --- | --- |
| C25 defaults settlement to adjudicate after D10 confirmed SELLER_FAVOR returned 200; buyer_close remains an explicit alternative pending a confirmed outcome code | C25, D10 owner findings | Accept | Use verified settlement behavior while unmapped buyer-close outcomes keep the reserve |
| C25 submits evidence only when fresh status and provide_evidence links permit it; otherwise the real shield blocks the proposal and the week continues | C25 | Accept | D8 and D9 showed that dispute stage alone cannot determine evidence availability |
| C25 CLI parses the database name and refuses names not ending in _demo or _test before opening connections | C25 | Accept | A resumable staged money workflow must never operate on the owner's working database |
| D10 maps RESOLVED_SELLER_FAVOUR to WON and sizes RESOLVED_BUYER_FAVOUR as LOST_FULL or LOST_PARTIAL from amount_refunded; CANCELED_BY_BUYER stays mock-only | D10, owner confirmed Oct 26 | Accept | Sandbox adjudication recorded INELIGIBLE_BUYER_PROTECTION_POLICY and accept-claim recorded SELLER_ISSUED_REFUND; unrecognized outcomes retain reserves |
| D10 confirms multipart input JSON evidence at WAITING_FOR_SELLER_RESPONSE / CHARGEBACK and JSON accept-claim, both returning 200; evidence submission then moved the case to UNDER_REVIEW | D10, owner confirmed Oct 26 | Accept | Existing adapter defaults match the observed formats and action links still determine evidence permission |
| C25 plans a GET-confirmed RETURNED line as a new attempt on that same UNPAID line in the run's exact replacement manifest; approval, simulation and run reconciliation include it | C25, owner approved | Accept | C's historical $300 must join Friday's single $1,086 approval without duplicating its obligation, allocation or executor write path |
| C25 keeps unavailable initial evidence proposals only when the real shield returns BLOCK, recording existing proposal rule_results and SHIELD_VERDICT audits; executable request construction stays strict | C25, owner approved | Accept | An unavailable action must remain visible as a refusal rather than fail before the shield can record its decision |

## Oct 26 C23b configured Gemini model

| Change | Source | Decision | Reason |
| --- | --- | --- | --- |
| C23b uses Google Gemini through google_genai and GOOGLE_API_KEY supplied only at runtime; absent configuration stays disabled, incomplete provider/key settings fail explicitly, and demo_week stays scripted unless --model configured is selected | Owner C23b prompt | Accept | The owner chose the AI Studio free tier; keys must never enter logs, errors, committed files or the toolkit child, and configured runs must not silently become scripted |
| C23b defaults configured Gemini runs to gemini-3.5-flash; COUNTERSIGN_LLM_MODEL can explicitly select gemini-3.5-flash-lite as the eval fallback or another exact ID, with no automatic switching | Owner free-key probe and Oct 26 clarification | Accept | Both selected models returned function calls in the owner's probe; access observations do not establish evaluation quality or authorize silent model changes |
| C23b pins the optional main llm extra to LangChain 1.4.7, core 1.7.0, Google integration 4.4.2 and Google SDK 2.33.0; the isolated toolkit child keeps PayPal SDK 1.11.0 with LangChain 0.3.23/core 0.3.76 in .venv/toolkit, selected by COUNTERSIGN_TOOLKIT_PYTHON | C23b dependency proof; LangChain signature guidance and Google deprecations | Accept | Google integration 2.x lacks Gemini 3 signature support and new projects may lack Gemini 2.5 access; the existing subprocess bridge separates incompatible LangChain generations |
| C23b retains original assistant messages only for the current bounded investigation and returns tool results as ToolMessages; signatures stay private and history clears on completion or error | C23b offline Google transport tests | Accept | Reconstructing Gemini messages loses signed text and function-call metadata; tests preserve both across two tool turns for Flash and Flash-Lite |
| C23b configured demo uses genuine read-tool startup and stops on an unexpected actual-model decision; scripted checkpoint identities remain compatible | C23b | Accept | Real reasoning is not guaranteed to reproduce the staged script or deliberately propose an unsafe red-team action |
| Model calls get 3 total attempts (langchain-google-genai counts max_retries as attempts) and a 90 s timeout, instead of 1 attempt and 30 s | Owner (real-model rehearsal, Oct 26) | Accept | a single Gemini 503 or a slow first reply stopped the demo week; model calls only read and propose, so bounded retries cannot move money |

## Oct 26 sandbox findings (C08/C09; details in docs/SANDBOX_FINDINGS.md)

| Change | Source | Decision | Reason |
| --- | --- | --- | --- |
| Disputes for demos and the slice are staged only through the sandbox Resolution Center; API dispute creation is recorded BLOCKED | D8 | Accept | the API returned 400 "No permissions to set target_client_id" even with the disputes/create scope (F5) |
| A dispute hold is read from the capture status (PENDING), never from withheld_balance; cash stays available-only and DISPUTE_EARMARK stays a non-cash reserve | D8, D12 | Accept | withheld stayed 0.00 in all 585 readings while every disputed capture turned PENDING |
| Demo narration says the disputed capture is frozen (PENDING) and CounterSign reserves the share; it never claims "$400 withheld" | D8, D12 | Accept | HANDOFF section 10's withheld column did not happen in the sandbox; claims must match recordings |
| Evidence is autonomous only while WAITING_FOR_SELLER_RESPONSE with a provide_evidence link; otherwise the shield blocks it and the demo shows the block | D9, D10 | Accept (already in C24) | the $400 item-not-received dispute went straight to UNDER_REVIEW with no evidence link; multipart evidence at WAITING/CHARGEBACK returned 200 |
| OUTCOME_MAP adds CANCELED_BY_BUYER -> CANCELLED (no refund allowed; any refund gives UNKNOWN_OUTCOME) | D10 | Accept | the owner's buyer close resolved as CANCELED_BY_BUYER / BUYER_CANCELLED_CASE |
| Defaults confirmed: evidence multipart, accept-claim JSON, settlement adjudicate, PAYPAL_REFUND_SOURCE=paypal:available | D10, D12 | Accept | each returned 200 in D10, and withheld was never used, so a refund can only come from available |
| F11 corrected: simulator events can pass postback verification; we still verify every event, and the mock keeps its test-only signer | D11 | Accept | the D11 simulator event verified SUCCESS, like all 46 real deliveries |
| GET /v1/notifications/webhooks-events is the webhook backfill source; resend can generate duplicate-delivery faults | D11 | Accept | it listed 47 events, including 2 never delivered to us, and one resend arrived |
| The owner's public IP was redacted from published recordings (tunnel host headers and registration URLs read TUNNEL.ngrok-free.app) | C09 secret scan | Accept | the ngrok hostname embeds the caller's IP; no other private value was found in 1,129 files |
## Oct 26 C23c mock rehearsal

| Change | Source | Decision | Reason |
| --- | --- | --- | --- |
| Configured mock demo loads only COUNTERSIGN_LLM_PROVIDER, COUNTERSIGN_LLM_MODEL and GOOGLE_API_KEY from the selected runtime file, without interpolation; process values win and scripted mock/receiver read no file | C23c | Accept | A real model must be configurable for a mock rehearsal without importing sandbox PayPal credentials, owner settings or a database URL from the same file |
| Toolkit Context remains sandbox-only while the selected client origin is explicitly the configured loopback mock or sandbox origin; every invocation rejects origin drift and every HTTP request stays behind the existing OAuth/fixed-GET guard | C23c | Accept | Context selects SDK defaults, not permission to contact another host, enable financial tools or fall back from the mock to PayPal |
| Fresh mock orders and captures use synthetic uppercase 32-character IDs compatible with the genuine SDK schemas; saved identities are never rewritten | C23c reproduction | Accept | Long hyphenated demo order IDs failed SDK validation before HTTP and surfaced as TOOLKIT_CALL_FAILED even though get_dispute reached the configured mock successfully |
| The isolated child adapts only SDK 1.11.0's missing tracking-by-order helper and transaction-search GET payload/JSON conventions, retaining the genuine five handlers and guarded read paths | C23c real-child tests | Accept | These are SDK client-interface defects, not missing mock endpoints; compatibility must not introduce writes, extra tools or alternate hosts |
| Mock rehearsal setup starts a loopback mock with a minimal environment, reuses its webhook and preserves existing state unless --reset is explicitly supplied | C23c | Accept | The owner needs reproducible setup without leaking model keys to the mock, duplicating callbacks or silently discarding another rehearsal |
| Configured demo incident subjects contain fixed action codes and resource identifiers; descriptive staging is recorded separately without granting permission | C23c integration review | Accept | IncidentService rejects prose in subjects, so the previous configured goal string failed before the next model step |

## Oct 26 C23d bounded read recovery

| Change | Source | Decision | Reason |
| --- | --- | --- | --- |
| Fixed recoverable read errors become tool observations, with at most three observed failures, sixteen total calls and eight model rounds per step; a fourth failure or exceeded call budget aborts without storing a proposal | C23d rehearsal | Accept | A mistaken resource ID must be correctable in the same signed conversation, while capability, request-guard, configuration and protocol failures remain fatal; only complete ordinary error frames keep the isolated toolkit open |
| The model-only incident projection labels internal subject IDs countersign_* and supplies linked paypal_order_id, paypal_capture_id and paypal_dispute_id from stored records; fixed trusted instructions name each tool's ID namespace | C23d rehearsal | Accept | An internal order UUID is not a PayPal order ID; explicit labels prevent confusing identifiers without changing dashboard views or promoting buyer text into the system prompt |
| A real-model commander step gets a 300 s budget (scripted models keep 60 s) | C23d real Gemini rehearsal | Accept | Gemini made three correct reads but the 60 s step budget expired mid-reply; one model call alone may take 90 s |

## Oct 26 C23e proposal params

| Change | Source | Decision | Reason |
| --- | --- | --- | --- |
| A shared ACTION_PARAMS table generates one trusted prompt line per action and validates target keys, structured evidence and reference prefixes before returning a proposal; one correction is allowed, then a second invalid proposal raises COMMANDER_PROPOSAL_INVALID within the existing round/time budgets | C23e rehearsal | Accept | Missing dispute params must be corrected before binding; signed Gemini assistant messages survive fixed human feedback, while stored-record matching, amounts and permission remain shield/binding decisions |
| Only PAYEE_INTEGRITY's fixed PAYEE_KEYS list is tolerated as policy-only fields, excluded from valid prompt params, with required targets still enforced; other unknown keys trigger INVALID_PROPOSAL_PARAMS | C23e, owner approved | Accept | Recognized destination attacks must reach the shield and retain PAYEE_INTEGRITY plus INJECTION_GUARD refusals and injection flags in the dashboard/audit rather than disappear as parser errors |
| An agent proposal that raises ApprovalError during locked binding rolls back entirely and records one RECONCILE_MISMATCH audit with reason AGENT_PROPOSAL_UNBOUND and count=1; the incident stays retryable and tick.errors excludes this recoverable agent miss | C23e rehearsal | Accept | A model's unresolved reference must not stop the demo or persist an unbound proposal; the audit counts misses without changing facade contracts, while deterministic owner/executor callers still raise and database/audit failures still fail the tick |
| After two agent failures (failed step or unbound proposal) at the same incident version, the agent skips that incident until its version changes | C23e review | Accept | The agent picks the oldest open incident without a proposal; without a cap one bad incident would call the model every tick and starve every newer incident |

## Oct 27 C25b no-hold demo

| Change | Source | Decision | Reason |
| --- | --- | --- | --- |
| C25b narrates a dispute freeze confirmed by capture GET PENDING plus our non-cash DISPUTE_EARMARK reserve, never a balance hold; dispute opening and seller-favour resolution leave available unchanged | D8, D12; owner C25b prompt | Accept | The demo must match recorded sandbox behavior rather than invent $400 or $60 withheld cash |
| C25b compares the protected $1,086 Friday run with a read-only $1,500 naive run against $1,800 available; the naive run pays $414 disputed shares with zero shortfall | C25b | Accept | The real risk is paying disputed obligations from an apparently sufficient balance; funds sufficiency cannot replace the reserve rule |

## Oct 27 C27 hosting

| Change | Source | Decision | Reason |
| --- | --- | --- | --- |
| The hosted demo is one Docker container: app and dashboard on $PORT, the PayPal mock on loopback 8099 and an embedded PostgreSQL 16 on loopback 55433, rebuilt on every boot | C27 free-tier research | Proposed (owner approval) | The mock is in-memory, so a disposable database always matches it, and no free managed PostgreSQL (Render expires after 30 days) has to outlive judging |
| Render free Docker web service is the recommended host; Google Cloud Run with the same image is the runner-up | C27 free-tier research (Oct 27) | Proposed (owner chooses) | Render needs no card and 750 free hours cover one service all month; Cloud Run boots faster but needs a billing account; Fly.io has no free tier and Hugging Face Docker Spaces now need PRO |
| The hosted app is built by create_hosted_app(): demo mode on the loopback mock only, no background workers, dashboard from web/ mounted after every API route | C27 | Accept | Live mode must be impossible on the public instance, and only the staged demo CLI or the M17 bar may tick the engine, as with demo_week --serve |
| The hosted boot refuses PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET and GOOGLE_API_KEY, naming the variable only, and forces COUNTERSIGN_MODE=demo, the mock URL and fake mock credentials | C27 | Accept | The public demo must never hold sandbox or model credentials, and a platform setting must not be able to switch it to live |
| Migrations run at image build into an empty template cluster; each boot copies it and sets a random database password | C27 boot measured at 0.1 CPU | Accept | initdb and a no-op alembic run cost about 50 s of a 160 s boot on Render-sized CPU; the template is built from the same image's migrations |
| The scripted demo week at boot is optional (COUNTERSIGN_HOSTED_SEED=week) and off by default | C27 local run | Accept | demo_week seeds balance_refresh_seconds=1 and the mock keeps every snapshot, reaching 813 MB over the week; a 512 MB host would restart in a loop |
| The owner chose Google Cloud Run (free credits) over Render for the hosted demo, using the same single-container image | Owner, Oct 27 | Accept | About 8 s cold start at 1 vCPU instead of Render's 1 min wake plus 85 s boot at 0.1 CPU; render.yaml stays as an unused alternative |

## Oct 27 C25c configured week

| Change | Source | Decision | Reason |
| --- | --- | --- | --- |
| Configured weeks check ID-matched resource GETs, reserved shares, one settlement per line and GET-confirmed returns before replacement; cash is starting available plus confirmed returns minus confirmed payouts/refunds, with stale snapshots pending reconciliation | C25c real-model rehearsal | Accept | A safe Monday cancellation must not fail Wednesday's staged cash number; scripted runs retain their strict timeline assertions |
| A completed matching cancel/evidence action is reused with CONFIGURED_MODEL_ACTED_EARLIER and its recorded action time; optional model differences remain logged and the final configured summary names shown and missing safety moments | C25c | Accept | Real reasoning may act early or refuse an unsafe demonstration; the script must neither force a staged proposal nor claim a safety moment it did not observe |
| Numeric provider HTTP 429 becomes MODEL_QUOTA_EXHAUSTED, separately audited outside the per-incident failure cap and saved as a resumable script stop; no provider error text or automatic model switch | C25c | Accept | A free-tier daily limit is not an incident failure and must not strand the same incident when the owner resumes after quota resets |
| The walkthrough recommends explicit gemini-3.5-flash-lite for rehearsals, while evidence-prefix guidance is generated from EVIDENCE_PREFIXES | Owner Oct 27 quota observations; C25c | Accept | The owner's Flash free-tier limit was 20 daily requests, below a full week's observed needs, and fixed shape guidance avoids wasting correction turns |

## Oct 27 C29 documentation sync

| Change | Source | Decision | Reason |
| --- | --- | --- | --- |
| HANDOFF synced with sandbox findings (owner-approved Oct 27); no design change. | Owner C29 prompt | Accept | Describe the recorded D1–D13 outcomes, implemented recovery and evidence gates, and C25b's fixed demo amounts without treating them as a replay of the recorded capture cash flows |

## Oct 27 M13 baseline_llm

| Change | Source | Decision | Reason |
| --- | --- | --- | --- |
| baseline_llm calls the local mock itself through ten fixed tools (CONTRACTS 6.2 list plus request_owner_approval); no shield, ledger or approval binding, and approvals are only shown to the model | M13, F18 | Accept | A plain agent must be compared on its own judgement, while still using the same M11 owner policies as the other systems |
| The model is told the owner's business rules (shares, dispute hold, owner approval tiers, untrusted buyer text) in plain words | M13 | Accept | Withholding the rules would make the baseline a strawman; only enforcement in code is absent |
| create_payout maps a model-chosen reference to one stable sender_batch_id/PayPal-Request-Id and frozen body, follows the duplicate 400 details[].link by GET, and refuses reuse of a reference for a different payment | M13, D6 | Accept | F18 requires PayPal's documented idempotency used correctly so the comparison is fair |
| The model names maker keys only; tools take emails from owner maker records and never show emails or credentials to the model | M13 | Accept | Keeps PII and mock secrets out of model traffic; payee changes from buyer text still need the model to misuse a maker key |
| The baseline refuses non-loopback URLs, non-mock ground and credentials not prefixed mock-, and reads only initial_state minus the mock seed | M13, F18 | Accept | It must never receive sandbox credentials or graded answers |
| A numeric HTTP 429 or MODEL_QUOTA_EXHAUSTED stops the system; the run row error is MODEL_QUOTA_EXHAUSTED and run_suite stops, writing only runs.partial.jsonl | M13 | Accept | A quota stop is not a baseline failure, and a partial comparison must not produce a summary or pass^k |
| run_one/run_suite accept mock_transport="tcp", serving the same in-process mock app via uvicorn on an ephemeral 127.0.0.1 port; "asgi" stays the default | M13, M16 follow-up | Accept | CounterSign's genuine toolkit child needs a TCP-reachable mock for configured-model runs, while the runner keeps clock and webhook control |
## Oct 27 M17 demo control bar

| Change | Source | Decision | Reason |
| --- | --- | --- | --- |
| The demo bar's step and reset replay C25's scripted mock week (`scripts/demo_week.py`), one checkpoint step per press, instead of the M11 eval runner on `demo_week.json` named in CONTRACTS section 3; the endpoint paths and state fields are unchanged plus additive fields | M17 | Proposed (owner approval; contract wording) | The hosted container already seeds this exact week, the video and walkthroughs use its amounts, and the eval runner needs an `_eval`/`_test` database the hosted `_demo` one is not |
| `demo_week --pause-after-step` stops after each newly SUCCEEDED checkpoint step (never the last); the app runs it as a subprocess with fixed arguments and keeps only a fixed exit code | M17 | Accept | A saved step boundary is exactly where resume already starts; a subprocess keeps the week's global mock outcome mapping, clock and pools out of the app and works with no background workers |
| `demo_week --reset-mock-week` truncates the same fixed tables as Engine.reset, resets the mock and re-registers its webhook, refusing unless target mock, demo settings on loopback, a `_demo`/`_test` database, every saved run idle and the webhook ID unchanged; the bar then uses a new sibling root and keeps old checkpoints | M17 | Accept | Engine.reset deliberately refuses `_demo` (sandbox evidence lives there); a mock-only CLI path restarts the disposable hosted week without weakening that guard |
| Demo endpoints mount only when COUNTERSIGN_DEMO_ROOT/APP_URL are set and settings are demo mode, a loopback mock origin and a `_demo`/`_test` database; every request rechecks; POST bodies are ignored | M17 | Accept | Live mode must not expose them, and the presenter can only advance the fixed staged week through the same shield and executor path |
| hosted_start.sh exports COUNTERSIGN_DEMO_ROOT (the boot seed's checkpoint) and COUNTERSIGN_DEMO_APP_URL | M17 | Accept | The bar continues the same run as the optional boot seed; the checkpoint lock refuses a step while that seed is still running |
| Incident titles come from a new view revision (migration 0005): PayPal dispute ID, maker display name and amount; payout attempt state, maker and amount; run week; staged goal or reconciliation code in plain words. A subject `title` is no longer used | M17 | Accept | Plain kind names made the desk unreadable on real data; fixed record fields can never carry buyer text, and the integration head assertions move to 0005 |
| DbSource marks every dispute `staged: true` when PayPal is the local mock, and the UI matches staged disputes by PayPal ID too | M17 real-data check | Accept | Every mock dispute is opened through the staged control, but the mock does not echo `staged`; real data showed no staged labels at all |
| `web/` is served with `Cache-Control: no-cache` for pages, .mjs, .css and .json (ETag revalidation) and .mjs as text/javascript | M17 | Accept | Browsers combined cached and redeployed modules after a deploy; some MIME tables lack .mjs |
## Oct 27 C25d same-step action reuse

| Change | Source | Decision | Reason |
| --- | --- | --- | --- |
| Configured demo goals recheck EXECUTED cancel/evidence proposals on other incidents after each tick and on resume, with matching action/target params and a fresh matching RETURNED GET for cancel | C25d owner rehearsal | Accept | A real model can safely finish the goal on the original incident during the staged step; the artificial duplicate must not turn that success into a different-decision stop |
| A matching shield-blocked staged duplicate is recorded once as CONFIGURED_MODEL_DUPLICATE_BLOCKED only when an executed match exists elsewhere; ordinary unmatched blocks still stop honestly and scripted behavior is unchanged | C25d | Accept | Reusing completed work must preserve the real shield refusal, never convert a BLOCK into permission or issue a second financial write |
| Satisfied staged goals enter WAITING_EXTERNAL through IncidentService's existing investigation/wait transitions, including saved-stop recovery; no FSM or reconciler agreement is bypassed | C25d existing lifecycle constraint | Accept | RESOLVED is only available through reconciler agreement, which generic staged subjects cannot satisfy; parking removes these artificial goals from model selection while explicitly reporting that they are not terminal |

## Oct 27 C30 dispute incident identity

| Change | Source | Decision | Reason |
| --- | --- | --- | --- |
| Inbox matches DISPUTE incidents by subject.dispute_id containment, and discovery prefers an existing live case over resolved history; both lookup/insert paths retain the transaction-scoped owner-decision lock, without a schema change | C30 M17 real-data check | Accept | Exact JSON equality treated discovery's extra PayPal/order/maker fields as a new dispute; serialized identity matching prevents duplicates in either arrival order while preserving proposals, approvals and reconciler-owned reopening |
