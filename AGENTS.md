# CounterSign: rules for coding agents

PayPal AI Hackathon project. Deadline: Nov 13, 2026, 1:30 AM IST (Nov 12, 12:00 PM PT).

Before any design or build work, read these fully:
1. `docs/HANDOFF.md`: the source of truth for the design.
2. `docs/DECISIONS.md`: why each design choice was made.
3. `docs/PLAN.md`: the day one sandbox plan, PostgreSQL schema and state machines.
4. `docs/CONTRACTS.md`: the interfaces between owners. Build against these exactly.
5. `docs/TASKS.md`: your task card, and the files it allows you to touch.

## Hard rules (copied from CLAUDE.md)

- The AI agent never calls money moving PayPal APIs. Only the adapter writes, after the shield allows it.
- Never enable PayPal Agent Toolkit write tools (accept_dispute_claim, create_refund, etc.) for the agent.
- PostgreSQL only. No graph database, no separate message queue.
- Sandbox only. Never commit secrets; credentials live in .env. Never read, print or log .env values.
- Do not assume PayPal behaviour marked "verify" in HANDOFF.md section 9; test it first.
- Record every design change with a one line reason in docs/DECISIONS.md.

Two consequences of these rules, both easy to get wrong:
- In the Python Agent Toolkit, the config key `disputes.create` enables `accept_dispute_claim`. Never set it.
- The evaluation baseline agent runs only against `mock_paypal`. It never receives sandbox credentials.

## Folder ownership

| Owner | Paths |
| --- | --- |
| **core** | `src/countersign/{fsm,shield,executor,ledger,paypal,inbox,reconcile,agent,db,approvals,simulator,incidents}/`, `migrations/`, `scripts/sandbox_checks/` |
| **core** (paths not named in the original split) | `src/countersign/api/` except `dashboard.py`; `src/countersign/{harness,main,worker,config,money}.py`; `scripts/` (other than `sandbox_checks/`); `recordings/`; `tests/` except `tests/mock_paypal/`, `tests/eval/` and `tests/dashboard/` |
| **mate** | `mock_paypal/`, `eval/`, `web/`, `src/countersign/api/dashboard.py`, `tests/mock_paypal/`, `tests/eval/`, `tests/dashboard/` |
| **shared** (owner approval required) | `docs/`, `AGENTS.md`, `CLAUDE.md`, `pyproject.toml`, and every root file (`README.md`, `LICENSE`, `.gitignore`, `.env.example`, `docker-compose.yml`, deploy config) |

- Anyone may **read** any file. Write only inside the folders your owner owns.
- **Never edit files outside your owned folders.** If you need a change there, stop and say so in your task output. Do not work around it.
- **If a contract in `docs/CONTRACTS.md` must change, stop and say so.** Contract changes need owner approval and a version bump in CONTRACTS.md.
- A path marked **[pending Dn]** in CONTRACTS.md depends on a day one sandbox check. Implement the documented default behind a config switch, and do not hard-code a guess elsewhere.

## Workflow

- **One branch per task**, named `<owner>/<task-id>-<slug>`, for example `core/C03-runner-d1` or `mate/M02-payouts`. Branch from `main`.
- **Small commits.** Each commit message starts with the task id, for example `C03: add D1 token scope check`.
- **Tests come with every change.** A task is done only when its acceptance criteria in `docs/TASKS.md` pass, together with `pytest`, `ruff check` and the import-linter contracts.
- **Never read or print `.env`.** Code loads it at runtime with `python-dotenv`. Logs, recordings, test output and error messages must never contain tokens, client IDs, secrets or real emails.
- **Recordings are public.** Redact them before committing (`docs/PLAN.md` section 1.2).
- Do not add dependencies outside your task card. `pyproject.toml` is shared, so ask first.
- Money is integer minor units plus a currency code. Never use floats for money.
