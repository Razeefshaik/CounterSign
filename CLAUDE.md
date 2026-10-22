# CounterSign

PayPal AI Hackathon project. Deadline: Nov 13, 2026, 1:30 AM IST.

Before any design or build work, read docs/HANDOFF.md and docs/DECISIONS.md fully.
HANDOFF.md is the source of truth for the design. docs/PLAN.md holds the approved day one plan, schema and state machines.

Hard rules:
- The AI agent never calls money moving PayPal APIs. Only the adapter writes, after the shield allows it.
- Never enable PayPal Agent Toolkit write tools (accept_dispute_claim, create_refund, etc.) for the agent.
- PostgreSQL only. No graph database, no separate message queue.
- Sandbox only. Never commit secrets; credentials live in .env. Never read, print or log .env values.
- Do not assume PayPal behaviour marked "verify" in HANDOFF.md section 9; test it first.
- Record every design change with a one line reason in docs/DECISIONS.md.