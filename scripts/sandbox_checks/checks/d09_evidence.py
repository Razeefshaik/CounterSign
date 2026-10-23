"""D9: can tracking evidence be submitted on the item-not-received dispute, and at which stage?

The docs conflict on the stage (claim stage only vs inquiry) and on the format (multipart
vs JSON), so both formats are tried and the stage is recorded (F7). If PayPal offers no
provide_evidence link at inquiry, D10 escalates and records the stage that accepts it.

Tracking comes from D2's saved data (data["tracking"]["inr_400"] with "carrier" and
"tracking_number"); when D2 has none, the operator is asked.
"""

import json

from ..runner import CheckContext, Step
from .d08_dispute_create import (
    classify,
    get_dispute,
    has_rel,
    poll_dispute,
    resource_id,
    send_once,
    summarize,
    webhook_deliveries,
    write_findings,
)

AFTER_MAX_POLLS = 10


def evidence_body(tracking: dict) -> dict:
    return {
        "evidences": [
            {
                "evidence_type": "PROOF_OF_FULFILLMENT",
                "evidence_info": {
                    "tracking_info": [
                        {
                            "carrier_name": tracking["carrier"],
                            "tracking_number": tracking["tracking_number"],
                        }
                    ]
                },
                "notes": "Tracking for this order, uploaded by the marketplace (staged sandbox).",
            }
        ]
    }


async def submit_evidence(ctx: CheckContext, dispute_id: str, tracking: dict, *, step: str):
    """Multipart with an input JSON part first; a JSON body only after a definite rejection.

    Each attempt is checkpointed before it is sent and never re-sent on resume. An
    ambiguous attempt (lost response, interruption, 5xx) stays UNKNOWN for the caller to
    reconcile by GET, so PayPal never receives the same evidence twice.
    """
    body = evidence_body(tracking)
    attempts = []
    for fmt, kwargs in (
        ("multipart", {"files": {"input": (None, json.dumps(body), "application/json")}}),
        ("json", {"body": body}),
    ):
        result = await send_once(
            ctx, resource_id(dispute_id), "provide-evidence", key=f"{step}:{fmt}", **kwargs
        )
        outcome = classify(result)
        attempts.append(
            {
                "format": fmt,
                "status": result["status"],
                "error_name": result.get("error_name"),
                "outcome": outcome,
            }
        )
        if outcome != "REJECTED":
            break
    last = attempts[-1]
    return {
        "submitted": last["outcome"] == "SENT",
        "outcome": last["outcome"],
        "format": last["format"] if last["outcome"] in ("SENT", "UNKNOWN") else None,
        "attempts": attempts,
    }


async def load_inputs(ctx: CheckContext) -> dict:
    d8 = ctx.load_check_data("D8")
    dispute_id = ((d8.get("disputes") or {}).get("inr_400") or {}).get("dispute_id")
    tracking = dict(ctx.data.get("tracking") or {})
    if not tracking:
        saved = ((ctx.load_check_data("D2").get("tracking") or {}).get("inr_400")) or {}
        if saved.get("carrier") and saved.get("tracking_number"):
            tracking = {"carrier": saved["carrier"], "tracking_number": saved["tracking_number"]}
        else:
            tracking = {
                "carrier": await ctx.manual.ask_id(
                    "Enter the carrier name used in D2 (e.g. USPS)."
                ),
                "tracking_number": await ctx.manual.ask_id("Enter the tracking number from D2."),
            }
    return {"dispute_id": dispute_id, "tracking": tracking}


async def inspect_links(ctx: CheckContext) -> dict:
    if not ctx.data["dispute_id"]:
        return {"before": None}
    detail = await get_dispute(ctx, ctx.data["dispute_id"], step="before_evidence")
    return {"before": summarize(detail)}


async def provide_evidence(ctx: CheckContext) -> dict:
    before = ctx.data["before"]
    if before is None:
        return {"evidence": {"submitted": False, "reason": "D8 found no item-not-received dispute"}}
    if not has_rel(before["rels"], "provide_evidence"):
        ctx.note(
            f"D9: no provide_evidence link at stage {before['dispute_life_cycle_stage']}; "
            "D10 escalates and records which stage accepts evidence."
        )
        return {
            "evidence": {
                "submitted": False,
                "reason": "provide_evidence link absent",
                "stage": before["dispute_life_cycle_stage"],
                "status": before["status"],
            }
        }
    result = await submit_evidence(
        ctx, ctx.data["dispute_id"], ctx.data["tracking"], step="evidence"
    )
    result.update(stage=before["dispute_life_cycle_stage"], status=before["status"])
    return {"evidence": result}


def accepted(evidence: dict) -> bool:
    return bool(evidence.get("submitted") or evidence.get("reconciled") == "ACCEPTED")


async def observe_after(ctx: CheckContext) -> dict:
    evidence = dict(ctx.data["evidence"])
    if not evidence.get("submitted") and evidence.get("outcome") != "UNKNOWN":
        return {"after": None, "after_history": []}
    status = ctx.data["before"]["status"]
    detail, history = await poll_dispute(
        ctx,
        ctx.data["dispute_id"],
        done=lambda detail: detail.get("status") != status,
        step="after_evidence",
        max_polls=AFTER_MAX_POLLS,
    )
    if evidence.get("outcome") == "UNKNOWN":
        # The response was lost: a status change after the attempt shows PayPal took it.
        evidence["reconciled"] = "ACCEPTED" if detail.get("status") != status else "UNKNOWN"
    return {"after": summarize(detail), "after_history": history, "evidence": evidence}


async def findings(ctx: CheckContext) -> dict:
    evidence = ctx.data["evidence"]
    if accepted(evidence):
        verdict = "CONFIRMED" if evidence["stage"] == "INQUIRY" else "DIFFERENT"
    elif evidence.get("reason") == "provide_evidence link absent":
        verdict = "DIFFERENT"
    else:
        verdict = "BLOCKED"
    dispute_ids = [ctx.data["dispute_id"]] if ctx.data["dispute_id"] else []
    return write_findings(
        ctx,
        {
            "check_id": "D9",
            "verdict": verdict,
            "dispute_id": ctx.data["dispute_id"],
            "evidence_accepted_stage": evidence.get("stage") if accepted(evidence) else None,
            "evidence": evidence,
            "before": ctx.data["before"],
            "after": ctx.data["after"],
            "after_history": ctx.data["after_history"],
            "updated_webhooks": webhook_deliveries(ctx, "CUSTOMER.DISPUTE.UPDATED", dispute_ids),
            "notes": [
                "PayPal's tracking data is what the merchant uploaded, not carrier proof (F10)",
                "if no link at inquiry, see D10 findings for the stage that accepted evidence",
            ],
        },
    )


def get_steps() -> list[Step]:
    return [
        Step("load_inputs", load_inputs),
        Step("inspect_links", inspect_links),
        Step("provide_evidence", provide_evidence),
        Step("observe_after", observe_after),
        Step("findings", findings),
    ]
