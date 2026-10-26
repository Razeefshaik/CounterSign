# C28: submission checklist

The deadline is **Nov 12, 2026, 12:00 PM PT = Nov 13, 1:30 AM IST**. Aim to
submit at least a few hours early. Devpost forms and YouTube processing can be
slow.

**[OWNER]** marks a step only the human owner can do: logging in to Devpost,
GitHub or YouTube, recording, or approving a shared file. Everything else can be
prepared by an agent and reviewed by the owner.

Sources: HANDOFF section 2 (rules), section 10 (demo), section 11 (evaluation),
section 12 (claims), section 16 (video); TASKS card C28; SANDBOX_FINDINGS.

## 1. Before anything else: fill the README placeholders

Each placeholder is an HTML comment in `README.md`, next to a visible
**Pending** line. Replace both. Never type a number that is not in a results file.

| Placeholder | Filled by | Source of the numbers |
| --- | --- | --- |
| `<!-- STATUS: ... -->` and the status note | C28, on submission day | What is actually merged and run |
| `<!-- RESULTS: ... M18/C26 ... -->` mock evaluation | M18 | `eval/results/**/report.md` |
| `<!-- RESULTS: ... C26 ... -->` sandbox integration | C26 | `scripts/sandbox_eval.py` output, ground `sandbox` only |
| `<!-- RESULTS: ... C25 sandbox run ... -->` demo week | Owner-operated C25 sandbox run | `recordings/demo_week/<date>/run.jsonl` |
| `<!-- RESULTS: ... configured-model run ... -->` real model | Owner-operated configured run | That run's log; record the exact model id |
| `<!-- HOSTED: ... -->` hosted URL | C27 | The deploy platform |

If a result does not exist by submission day, leave the line as **Pending** or
remove it. Do not estimate.

Also check that the README still matches the code: the dashboard note (fixtures
until M15), and the quickstart commands if `scripts/demo_week.py` or
`scripts/mock_rehearsal.py` changed.

## 2. Claims language check

Run this over `README.md`, the Devpost text and the video script:

```bash
grep -n -i -E "payroll|never moves twice|money never|wrongful refund|nothing shows|withheld|guarantee|carrier.confirm|100%|always safe" README.md
```

Every hit must be one of the allowed forms below, or be removed.

| Do not say | Say |
| --- | --- |
| "money never moves twice" | "each obligation is settled at most once inside CounterSign; zero violations of four named invariants across N fault scenarios" (only with the real N from M18) |
| "zero wrongful refunds" | "no refund without the owner's approval or above policy" |
| "nothing shows it" | "nothing connects a dispute to the maker payouts it affects" |
| "payroll" | "weekly maker payout run" |
| "$400 withheld" | "the disputed capture is frozen (PENDING) and CounterSign reserves the maker's share" (D8, D12) |
| "tracking proves delivery" | "merchant-uploaded tracking" (D2, F10) |
| "the AI won the dispute" | "the sandbox settled the dispute (staged)" |

Also confirm:

- [ ] The platform-products sentence is present, word for word: "Real
      marketplaces collecting for third parties may use PayPal's platform
      products; this project uses Payouts as a focused prototype of the
      decision and safety layer."
- [ ] Mock and sandbox results are in separate places, never added together.
- [ ] The baseline LLM agent is described as mock-only, never given sandbox
      credentials.
- [ ] Synthesised payout states are labelled "synthesised".
- [ ] The model is named: Google Gemini through LangChain, with the exact model
      id actually used (default `gemini-3.5-flash`).

## 3. Video (under 3 minutes; target 2:45) [OWNER]

Rules: a **YouTube** link, **under 3 minutes**, showing the project working. No
unlicensed music. No third-party trademarks beyond naming PayPal and the tools
used. Judges may judge from the description and video alone.

Label every staged action **on screen**: dispute creation (Resolution Center,
as the sandbox buyer), dispute settlement (sandbox control), the network drop,
the injected buyer note, and Maker C's typo payout. If a shot comes from the
mock, the "Simulated: mock PayPal" banner must be visible.

Suggested shot list, from HANDOFF section 16, adjusted for the sandbox findings:

| Time | Shot | Demo moment (HANDOFF section 10) | Notes |
| --- | --- | --- | --- |
| 0:00 to 0:15 | Hook: a naive agent pays a maker twice after a network drop, and pays for a disputed order | Naive vs CounterSign | Use the M19 replay view, or the simulator's read-only "naive" comparison ($1,800 available, so it pays $414 of disputed shares). Label it mock. |
| 0:15 to 0:35 | The marketplace's week; the cash view with its freshness time | Monday to Wednesday | Show the disputed capture as PENDING and our reserve. **Never say "$400 withheld".** |
| 0:35 to 1:05 | The AI reads a dispute and tracking, judges evidence strength | Autonomous evidence for Maker A | D9: the sandbox $400 dispute had no `provide_evidence` link. If so, show the shield's EVIDENCE_INTEGRITY block instead of a submission, and say why. Call tracking "merchant-uploaded". |
| 1:05 to 1:40 | Cancel C's unclaimed payout; GET shows RETURNED; owner approves the exact Friday run once | Thursday and Friday: $1,086 approved, $414 reserved, $714 available after | Show real sandbox resource ids (batch and item ids). The $60 claim stays waiting for the owner. |
| 1:40 to 2:05 | Network drop after PayPal accepts; retry with the same keys; one payment | Safety moment 1 | Label the drop as staged. Show the 400 with the original batch link (D6) and one item in the GET. |
| 2:05 to 2:30 | Blocked unsafe proposals, with rule names | Safety moments 2 and 3 | Injection note: PROMPT_INJECTION flag, INJECTION_GUARD and PAYEE_INTEGRITY. Settled obligation: SETTLEMENT. |
| 2:30 to 2:45 | Results table, model used, how judges can run it | Evaluation | Only real numbers. Mock and sandbox shown separately. Point to the README quickstart and the hosted URL. |

- [ ] Record on the sandbox where possible; any mock footage shows the banner.
- [ ] Final length checked under 3:00 on YouTube itself.
- [ ] Video visibility follows the Devpost rules (check the form), and the link
      opens while logged out.

## 4. GitHub repository [OWNER]

- [ ] The repository is **public**.
- [ ] `LICENSE` (Apache-2.0) is at the root, and the **About** section shows
      "Apache-2.0 license". If it does not, check that GitHub detected the
      licence file.
- [ ] About: a one-line description, for example "The AI proposes, the code
      countersigns: safe disputes and maker payouts on PayPal". Website: the
      hosted URL once C27 is done. Topics, for example `paypal`, `ai-agents`,
      `payouts`, `langchain`, `gemini`, `postgresql`.
- [ ] `main` contains the final README, and the README renders (the Mermaid
      diagram and the tables).
- [ ] No secrets in the history: `.env` is untracked, and recordings are
      redacted (the C09 secret scan passed; rescan anything recorded since).

## 5. Hosted URL (C27, pending)

- [ ] The hosted dashboard runs in **demo mode** against the bundled mock.
- [ ] Live mode is off on the hosted instance.
- [ ] Secrets live only in the platform's environment, never in the repo.
- [ ] It works from a clean start (M20) and at phone width.
- [ ] The URL is added to the README (`<!-- HOSTED -->`), the GitHub About
      website field and Devpost.

If hosting is not ready, the README's full setup instructions count as the
runnable demo (HANDOFF section 2). Say so plainly; do not link a broken URL.

## 6. Devpost form [OWNER]

Field names vary; follow the actual form. Typical fields and what goes in them:

- [ ] **Project name and tagline:** "CounterSign: the AI proposes, the code countersigns."
- [ ] **Description / story:** the problem (five pains, HANDOFF section 3), the
      one-line idea, what the AI does versus what code does, the demo week, the
      sandbox findings (including the four DIFFERENT ones and how the design
      changed), results (mock and sandbox separately), the platform-products
      sentence. Reuse the README text so the two never disagree.
- [ ] **AI tools used and how:** Google Gemini through LangChain for the
      Incident Commander (read-only tools; one structured proposal per step);
      the PayPal Agent Toolkit's five read-only tools; a scripted model for
      repeatable demos. State that toolkit write tools are never enabled.
      Optionally mention that AI coding agents helped build the code.
- [ ] **PayPal products used:** Payouts, Disputes, Orders v2, Payments v2,
      shipment tracking, Reporting balances, Webhooks, Agent Toolkit (read only).
- [ ] **Built with:** Python, FastAPI, PostgreSQL, Alembic, LangChain, Gemini,
      PayPal REST APIs, PayPal Agent Toolkit, AG Grid Community, Docker.
- [ ] **Repository link** (public), **video link** (YouTube), **hosted URL**
      (if C27 is done).
- [ ] **What changed since Oct 1:** this is a new project; first commit Oct 23, 2026.
- [ ] **Team members** added on Devpost, each as they wish to be named.
- [ ] **Prize categories:** select every one that applies (for example Best Use
      of PayPal plus AI, Best Use of Agentic Commerce, and the AG Grid sponsor
      prize, since the dashboard uses AG Grid Community).
- [ ] Preview the submission while logged out; every link opens.
- [ ] **Submit** before Nov 12, 12:00 PM PT (Nov 13, 1:30 AM IST).

## 7. Last technical checks

- [ ] Fresh clone; the README quickstart runs from top to bottom on the mock.
- [ ] `pytest`, `ruff check .`, `lint-imports` and `node --test web/tests/*.test.mjs` pass.
- [ ] `docs/DECISIONS.md` has a line for every design change made since the last review.
- [ ] Every link in the README opens.
