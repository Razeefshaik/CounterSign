# M21: three deterministic eval gaps

This is a targeted **mock, model-disabled** follow-up to
`../2026-10-27-model-none-c31/`, not a replacement full benchmark.
Evaluated adapter/test commit: `41ff10f` (source hash in manifest.json).
Only the three requested scenarios were rerun with CounterSign, k=5 (15 cells).
No scenarios, expected answers, grader, core code, model, or mock behavior changed.
All three scenario content hashes are preserved in manifest.json.

| Scenario | Classification | Before | After | Result |
| --- | --- | ---: | ---: | --- |
| F_key_window_expired_lost_response | Adapter observation gap | 0/5 | 5/5 | Consumed run approval proves scheduling started; record it once even while proposal EXECUTING |
| F_key_window_expired_webhooks_dropped | Same adapter gap | 0/5 | 5/5 | Same correction; lost webhooks do not invent a confirmed item |
| F_onhold_then_failed_replace_once | Real core planner gap | 0/5 | 0/5 | Diagnosed only; no core edit |

Before: the key-window traces have **no actions**. required_actions contains
SCHEDULE_PAYOUT_RUN, so correct_final_action=false. There is no executed/blocked
signature collision in these cases. The Engine's run approval is consumed, but
its proposal remains EXECUTING because the response/identity stays UNKNOWN.
Ground truth independently confirms one $486 SUCCESS item; task_completed=true.
The adapter formerly observed only terminal proposal states, hiding the actual
scheduling action. It now records SCHEDULE_PAYOUT_RUN only for a matching run
with a consumed approval in an EXECUTING proposal, once per proposal. It does not
record successful payment or resolve UNKNOWN; the Engine remains conservative
and the unmodified grader still checks money from ground truth. Unapproved,
unmatched and other nonterminal actions receive no success credit. A later
EXECUTED observation does not duplicate scheduling; subsequent blocks remain
visible for the unchanged signature check.

For ONHOLD then FAILED, correct_final_action=true (no required actions) but
paid=0/due=1 and task_completed=false. After the matching item GET confirms
FAILED, the line is UNPAID and its shares remain SCHEDULED with the existing
active allocation. ApprovalService._plan_replacements at
`src/countersign/approvals/service.py:464` only accepts `_returned(latest)` at
line 501; `_returned` at line 68 demands both mirrored and raw GET RETURNED.
Fresh-run selection also excludes allocated SCHEDULED shares, so planning raises
NO_PAYABLE_OBLIGATIONS (line 559). The same RETURNED-only predicate is used by
`_replacement_ids` around line 111, so changing only the planner would not suffice.

**Proposed core fix for Razeef/Claude:** generalize the identity-matched,
GET-confirmed not-paid predicate to FAILED as well as RETURNED at both planning
and replacement binding, preserving latest attempt identity, raw status/amount/
payee checks, last_checked_at, exact allocation, next attempt number, owner
approval, cash, existing line reuse and execution recheck. The shield already
accepts GET-confirmed FAILED in
`src/countersign/shield/rules/cancel_before_replace.py:10`. Test held items remain
blocked, a verified FAILED replacement pays once after approval, duplicate plans
cannot duplicate it, and stale/mismatched GETs fail closed. The expected answer
stays one $486 payout to B by +2d, with at most one owner escalation and no
mandatory action name. Core is unchanged in this PR.

Evidence:

- before.jsonl: the 15 original CounterSign rows copied without changing metrics.
- diagnosis.json: three fresh run_one pre-fix rows, trace actions and grader
  expected inputs, owner decisions, deadline and redacted ground-truth payment facts;
  uses the original f330b3f adapter with the unchanged grader.
- runs.jsonl, summary.json, report.md: all 15 post-fix cells; 10 pass, 5 remain
  incomplete, zero errors, all four measured invariant counts zero, no model
  calls or tokens. Deterministic repetitions are reproducibility checks, not
  independent model samples or proof of safety.
- manifest.json: base f330b3f, evaluated source identity, original scenario hashes.

The original 480-cell report is untouched. No combined 180/240 score is claimed:
the full 480-cell experiment was deliberately not rerun. No real PayPal/LLM calls
or environment-file reads were made.

Validation: full pytest **4004 passed, 5 skipped in 986.70 seconds** with the
PostgreSQL test database and isolated toolkit settings. Focused eval selection
52 passed; hosted smoke/existing guard selection 19 passed; adapter unit selection
26 passed (these overlap the full suite). Ruff clean, import-linter 3 kept,
47 web tests passed. Final Docker image completed the public HTTP smoke and all
three actual entrypoint credential-refusal probes. Hosting measurements and exact
owner deploy/smoke/rollback commands are in docs/walkthrough/c27-hosting.md.
