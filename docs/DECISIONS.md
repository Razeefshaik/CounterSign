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
