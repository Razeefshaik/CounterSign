"""D10: settle the D8 disputes and record each path and outcome (verify #4, F8, F17).

- $400 item not received: reach UNDER_REVIEW (after evidence, escalate, or a buyer
  escalation), then adjudicate SELLER_FAVOR. No doc says how a dispute reaches
  UNDER_REVIEW, so every step's status and stage is recorded.
- $60 not as described: accept-claim, with the script standing in for the owner.
- $25 spare: require-evidence (SELLER_EVIDENCE), evidence, then adjudicate BUYER_FAVOR.
- Fallback (owner, Oct 23): the buyer closes a dispute in the Resolution Center.
Every write is checkpointed before it is sent and never re-sent on resume.
"""

import json

from .. import timeline
from ..runner import CheckContext, Step
from .d08_dispute_create import (
    HOLD_INTERVAL_SECONDS,
    HOLD_OBSERVE_SECONDS,
    RESOLUTION_CENTER,
    STAGED,
    balance_snapshot,
    classify,
    find_dispute,
    has_rel,
    minor,
    now,
    poll_dispute,
    rels,
    send_once,
    summarize,
    webhook_deliveries,
    withheld_minor,
    write_findings,
)
from .d09_evidence import submit_evidence

REVIEW_MAX_POLLS = 30
RESOLVE_MAX_POLLS = 30
SETTLE_ROLES = ("inr_400", "snad_60", "spare_25")


def status(detail: dict) -> str | None:
    return detail.get("status")


def stage(detail: dict) -> str | None:
    return detail.get("dispute_life_cycle_stage")


def succeeded(result: dict) -> bool:
    return isinstance(result.get("status"), int) and 200 <= result["status"] < 300


async def act_once(
    ctx: CheckContext, role: str, action: str, *, key: str | None = None, body=None, files=None
) -> dict:
    return await send_once(
        ctx,
        ctx.data["disputes"][role],
        action,
        key=key or f"{role}:{action}",
        body=body,
        files=files,
    )


async def wait(ctx, role, entry, *, after: str, done, max_polls=REVIEW_MAX_POLLS) -> dict:
    detail, history = await poll_dispute(
        ctx, ctx.data["disputes"][role], done=done, step=f"{role}_poll", max_polls=max_polls
    )
    entry["path"].append({"after": after, "history": history})
    return detail


def settled_or_review(detail: dict) -> bool:
    return status(detail) in ("UNDER_REVIEW", "RESOLVED")


async def reach_under_review(ctx: CheckContext, role: str, entry: dict, *, evidence: bool) -> dict:
    detail = await wait(ctx, role, entry, after="start", done=lambda _: True, max_polls=1)
    if (
        not settled_or_review(detail)
        and stage(detail) == "INQUIRY"
        and has_rel(rels(detail), "escalate")
    ):
        entry["escalate"] = await act_once(ctx, role, "escalate", body={"note": STAGED})
        detail = await wait(
            ctx,
            role,
            entry,
            after="escalate",
            done=lambda d: settled_or_review(d) or stage(d) != "INQUIRY",
        )
    if (
        evidence
        and status(detail) == "WAITING_FOR_SELLER_RESPONSE"
        and has_rel(rels(detail), "provide_evidence")
    ):
        entry["evidence"] = await submit_evidence(
            ctx, ctx.data["disputes"][role], ctx.data["tracking"], step=f"{role}_evidence"
        )
        entry["evidence"]["stage"] = stage(detail)
        detail = await wait(ctx, role, entry, after="evidence", done=settled_or_review)
    if not settled_or_review(detail):
        await ctx.manual.confirm(
            f"D10: the {role} dispute ({ctx.data['disputes'][role]}) is not with PayPal yet "
            f"(status {status(detail)}). As buyer-1 in {RESOLUTION_CENTER}, escalate it to "
            "PayPal if the page offers that, then press Enter. Label: staged sandbox action."
        )
        detail = await wait(ctx, role, entry, after="buyer_escalation", done=settled_or_review)
    entry["reached_under_review"] = any(
        change["status"] == "UNDER_REVIEW" for step in entry["path"] for change in step["history"]
    )
    return detail


async def adjudicate(ctx, role, entry, detail, outcome: str) -> dict:
    if status(detail) == "UNDER_REVIEW" and has_rel(rels(detail), "adjudicate"):
        entry["adjudicate"] = await act_once(
            ctx, role, "adjudicate", body={"adjudication_outcome": outcome}
        )
        detail = await wait(
            ctx,
            role,
            entry,
            after="adjudicate",
            done=lambda d: status(d) == "RESOLVED",
            max_polls=RESOLVE_MAX_POLLS,
        )
    return detail


def close(entry: dict, detail: dict) -> dict:
    entry["final"] = summarize(detail)
    entry["resolved"] = status(detail) == "RESOLVED"
    return entry


def missing(role: str) -> dict:
    return {role: {"skipped": "D8 found no dispute", "resolved": False, "path": []}}


async def load_inputs(ctx: CheckContext) -> dict:
    d8 = ctx.load_check_data("D8")
    d9 = ctx.load_check_data("D9")
    disputes = {
        role: entry["dispute_id"]
        for role, entry in (d8.get("disputes") or {}).items()
        if isinstance(entry, dict) and entry.get("dispute_id")
    }
    return {
        "disputes": disputes,
        "captures": d8.get("captures") or {},
        "baseline_balance": d8.get("baseline_balance") or {},
        "tracking": d9.get("tracking") or {},
        "d9_evidence": d9.get("evidence") or {},
    }


async def settle_inr_400(ctx: CheckContext) -> dict:
    if "inr_400" not in ctx.data["disputes"]:
        return missing("inr_400")
    entry: dict = {"path": []}
    detail = await reach_under_review(
        ctx, "inr_400", entry, evidence=not ctx.data["d9_evidence"].get("submitted")
    )
    detail = await adjudicate(ctx, "inr_400", entry, detail, "SELLER_FAVOR")
    return {"inr_400": close(entry, detail)}


async def accept_claim_60(ctx: CheckContext) -> dict:
    if "snad_60" not in ctx.data["disputes"]:
        return missing("snad_60")
    entry: dict = {"path": [], "attempts": []}
    detail = await wait(ctx, "snad_60", entry, after="start", done=lambda _: True, max_polls=1)
    body = {"note": STAGED, "accept_claim_type": "REFUND"}
    # The docs disagree on JSON vs multipart. This POST refunds the buyer, so the next
    # format is sent only after a definite format rejection. An ambiguous attempt (lost
    # response, interruption, 5xx) stays UNKNOWN and is reconciled by GET, never re-sent:
    # a stale unresolved GET must not trigger a second accept-claim.
    for fmt, kwargs in (
        ("json", {"body": body}),
        ("multipart", {"files": {"input": (None, json.dumps(body), "application/json")}}),
    ):
        if status(detail) == "RESOLVED" or not has_rel(rels(detail), "accept_claim"):
            break
        result = await act_once(
            ctx, "snad_60", "accept-claim", key=f"snad_60:accept-claim:{fmt}", **kwargs
        )
        outcome = classify(result)
        entry["attempts"].append({"format": fmt, **result, "outcome": outcome})
        if outcome != "REJECTED":
            if outcome in ("SENT", "UNKNOWN"):
                detail = await wait(
                    ctx,
                    "snad_60",
                    entry,
                    after=f"accept_claim_{fmt}",
                    done=lambda d: status(d) == "RESOLVED",
                    max_polls=RESOLVE_MAX_POLLS,
                )
            break
    entry["accept_claim_outcome"] = (
        "RESOLVED"
        if status(detail) == "RESOLVED"
        else entry["attempts"][-1]["outcome"]
        if entry["attempts"]
        else "NOT_SENT"
    )
    return {"snad_60": close(entry, detail)}


async def settle_spare_25(ctx: CheckContext) -> dict:
    if "spare_25" not in ctx.data["disputes"]:
        return missing("spare_25")
    entry: dict = {"path": []}
    detail = await reach_under_review(ctx, "spare_25", entry, evidence=False)
    if status(detail) == "UNDER_REVIEW" and has_rel(rels(detail), "require_evidence"):
        entry["require_evidence"] = await act_once(
            ctx, "spare_25", "require-evidence", body={"action": "SELLER_EVIDENCE"}
        )
        detail = await wait(
            ctx,
            "spare_25",
            entry,
            after="require_evidence",
            done=lambda d: status(d) != "UNDER_REVIEW",
        )
        if status(detail) == "WAITING_FOR_SELLER_RESPONSE" and has_rel(
            rels(detail), "provide_evidence"
        ):
            entry["evidence"] = await submit_evidence(
                ctx,
                ctx.data["disputes"]["spare_25"],
                ctx.data["tracking"],
                step="spare_25_evidence",
            )
            entry["evidence"]["stage"] = stage(detail)
            detail = await wait(ctx, "spare_25", entry, after="evidence", done=settled_or_review)
    detail = await adjudicate(ctx, "spare_25", entry, detail, "BUYER_FAVOR")
    return {"spare_25": close(entry, detail)}


async def buyer_close_fallback(ctx: CheckContext) -> dict:
    entry = dict(ctx.data.get("buyer_close") or {})
    target = next(
        (role for role in ("inr_400", "spare_25") if not ctx.data[role]["resolved"]), None
    )
    if "dispute_id" not in entry and target and target in ctx.data["disputes"]:
        entry = {"role": target, "dispute_id": ctx.data["disputes"][target]}
    if "dispute_id" not in entry:
        if "capture_id" not in entry:
            value = await ctx.manual.ask_id(
                "D10 fallback: every dispute settled, so the buyer-close path needs one more. "
                "Enter the capture ID of another completed buyer-1 payment, or type skip."
            )
            if value.lower() == "skip":
                return {"buyer_close": {"skipped": True}}
            entry = {"role": "extra", "capture_id": value}
            ctx.checkpoint(buyer_close=entry)
        if "submitted_at" not in entry:
            await ctx.manual.confirm(
                f"D10 fallback: as buyer-1 in {RESOLUTION_CENTER}, report 'item not received' "
                f"on capture {entry['capture_id']}. Press Enter right after submitting. "
                "Label: staged sandbox action."
            )
            entry["submitted_at"] = now(ctx)
            ctx.checkpoint(buyer_close=entry)
        entry.update(
            await find_dispute(
                ctx,
                entry["capture_id"],
                submitted_at=entry["submitted_at"],
                skip=set(ctx.data["disputes"].values()),
                step="extra_find",
            )
        )
        ctx.checkpoint(buyer_close=entry)
        if "dispute_id" not in entry:
            return {"buyer_close": entry}
    if "closed_confirmed_at" not in entry:
        await ctx.manual.confirm(
            f"D10 fallback: as buyer-1 in {RESOLUTION_CENTER}, open dispute "
            f"{entry['dispute_id']} and close or cancel it (withdraw the case). Press Enter "
            "when done. Label: staged sandbox action."
        )
        entry["closed_confirmed_at"] = now(ctx)
        ctx.checkpoint(buyer_close=entry)
    detail, history = await poll_dispute(
        ctx,
        entry["dispute_id"],
        done=lambda d: status(d) == "RESOLVED",
        step="buyer_close_poll",
        max_polls=RESOLVE_MAX_POLLS,
    )
    entry["history"] = history
    entry["final"] = summarize(detail)
    entry["resolved"] = status(detail) == "RESOLVED"
    return {"buyer_close": entry}


def record_resolutions(ctx: CheckContext):
    """Timeline entries for D11 and D12: when each dispute was first seen RESOLVED."""
    fallback = ctx.data.get("buyer_close") or {}
    entries = [
        (role, ctx.data["disputes"].get(role), ctx.data.get(role) or {}) for role in SETTLE_ROLES
    ]
    entries.append((fallback.get("role"), fallback.get("dispute_id"), fallback))
    for role, dispute_id, entry in entries:
        histories = [step["history"] for step in entry.get("path") or []]
        histories.append(entry.get("history") or [])
        seen = next(
            (
                change["at"]
                for history in histories
                for change in history
                if change["status"] == "RESOLVED"
            ),
            None,
        )
        if not dispute_id or not seen:
            continue
        final = entry.get("final") or {}
        amount = final.get("dispute_amount") or {}
        timeline.record(
            ctx,
            "DISPUTE_RESOLVED",
            resource_id=dispute_id,
            amount_minor=minor(amount.get("value")),
            currency=amount.get("currency_code"),
            at=seen,
        )
        refund = (final.get("dispute_outcome") or {}).get("amount_refunded") or {}
        capture = ctx.data["captures"].get(role) or entry.get("capture_id")
        if minor(refund.get("value")) and capture:
            timeline.record(
                ctx,
                "CAPTURE_REFUNDED",
                resource_id=capture,
                amount_minor=minor(refund.get("value")),
                currency=refund.get("currency_code"),
                at=seen,
            )


async def observe_release(ctx: CheckContext) -> dict:
    """Balances every 5 minutes, up to 3 hours, until withheld is back to D8's baseline."""
    record_resolutions(ctx)
    observations = list(ctx.data.get("release_observations") or [])
    baseline = withheld_minor(ctx.data["baseline_balance"])
    total = HOLD_OBSERVE_SECONDS // HOLD_INTERVAL_SECONDS + 1

    def released(observation) -> bool:
        withheld = withheld_minor(observation)
        return baseline is not None and withheld is not None and withheld <= baseline

    while len(observations) < total and not (observations and released(observations[-1])):
        if observations:
            await ctx.sleep(HOLD_INTERVAL_SECONDS)
        observations.append(await balance_snapshot(ctx, step="release_balance"))
        ctx.checkpoint(release_observations=observations)
    first = next((o["fetched_at"] for o in observations if released(o)), None)
    valid = sum(withheld_minor(o) is not None for o in observations)
    # Failed reads or no baseline are missing evidence, not "never released".
    if first:
        evidence = "RELEASED"
    elif baseline is None or not valid:
        evidence = "BLOCKED"
    else:
        evidence = "NOT_RELEASED"
    return {
        "release_observations": observations,
        "withheld_back_to_baseline_at": first,
        "release_evidence": evidence,
        "release_valid_observations": valid,
    }


def outcomes(ctx: CheckContext) -> list[dict]:
    seen = []
    for role in (*SETTLE_ROLES, "buyer_close"):
        entry = ctx.data.get(role) or {}
        outcome = (entry.get("final") or {}).get("dispute_outcome") or {}
        if outcome.get("outcome_code"):
            seen.append(
                {
                    "outcome_code": outcome["outcome_code"],
                    "amount_refunded": outcome.get("amount_refunded"),
                    "role": entry.get("role", role),
                    "dispute_id": entry.get("dispute_id") or ctx.data["disputes"].get(role),
                    "path": [step["after"] for step in entry.get("path") or []] or ["buyer_close"],
                    "observed_at": now(ctx),
                }
            )
    return seen


async def outcome_catalogue(ctx: CheckContext) -> dict:
    path = ctx.root / "outcome_codes.json"
    catalogue = {"codes": {}}
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(existing, dict) and isinstance(existing.get("codes"), dict):
            catalogue = existing
    for item in outcomes(ctx):
        entries = catalogue["codes"].setdefault(item["outcome_code"], [])
        if not any(old.get("dispute_id") == item["dispute_id"] for old in entries):
            entries.append({key: value for key, value in item.items() if key != "outcome_code"})
    ctx.write_json(path, catalogue)
    return {"outcome_codes": sorted(catalogue["codes"])}


def evidence_stages(ctx: CheckContext) -> list[str]:
    stages = []
    for role in SETTLE_ROLES:
        evidence = (ctx.data.get(role) or {}).get("evidence") or {}
        if evidence.get("submitted"):
            stages.append(f"{role}:{evidence['stage']}")
    return stages


async def findings(ctx: CheckContext) -> dict:
    inr = ctx.data["inr_400"]
    adjudicated = inr.get("resolved") and succeeded(inr.get("adjudicate") or {})
    fallback = ctx.data["buyer_close"]
    if adjudicated:
        verdict = "CONFIRMED"
    elif fallback.get("resolved") or any(ctx.data[role].get("resolved") for role in SETTLE_ROLES):
        verdict = "DIFFERENT"
    else:
        verdict = "BLOCKED"
    dispute_ids = [*ctx.data["disputes"].values(), fallback.get("dispute_id")]
    return write_findings(
        ctx,
        {
            "check_id": "D10",
            "verdict": verdict,
            "settle_from_under_review": bool(adjudicated),
            "reached_under_review": {
                role: ctx.data[role].get("reached_under_review") for role in SETTLE_ROLES
            },
            "evidence_accepted_stages": evidence_stages(ctx),
            "inr_400": inr,
            "snad_60": ctx.data["snad_60"],
            "spare_25": ctx.data["spare_25"],
            "buyer_close": fallback,
            "outcome_codes": ctx.data["outcome_codes"],
            "withheld_back_to_baseline_at": ctx.data["withheld_back_to_baseline_at"],
            "release_evidence": ctx.data["release_evidence"],
            "resolved_webhooks": webhook_deliveries(
                ctx, "CUSTOMER.DISPUTE.RESOLVED", [i for i in dispute_ids if i]
            ),
            "notes": [
                "accept-claim was sent by the script standing in for the owner (staged)",
                "spare_25 evidence reuses the D9 tracking; it tests the stage, not the order",
            ],
        },
    )


def get_steps() -> list[Step]:
    return [
        Step("load_inputs", load_inputs),
        Step("settle_inr_400", settle_inr_400),
        Step("accept_claim_60", accept_claim_60),
        Step("settle_spare_25", settle_spare_25),
        Step("buyer_close_fallback", buyer_close_fallback),
        Step("observe_release", observe_release),
        Step("outcome_catalogue", outcome_catalogue),
        Step("findings", findings),
    ]
