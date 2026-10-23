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
    get_dispute,
    has_rel,
    poll_dispute,
    resource_id,
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
    """Try multipart with an input JSON part, then a JSON body; stop at the first 2xx.

    Evidence moves no money, so trying the second format after a failure is safe.
    """
    body = evidence_body(tracking)
    path = f"/v1/customer/disputes/{resource_id(dispute_id)}/provide-evidence"
    attempts = []
    for fmt, kwargs in (
        ("multipart", {"files": {"input": (None, json.dumps(body), "application/json")}}),
        ("json", {"json": body}),
    ):
        response = await ctx.client.request("POST", path, step=f"{step}_{fmt}", **kwargs)
        try:
            error_name = response.json().get("name")
        except (ValueError, AttributeError):
            error_name = None
        attempts.append({"format": fmt, "status": response.status_code, "error_name": error_name})
        if 200 <= response.status_code < 300:
            return {"submitted": True, "format": fmt, "attempts": attempts}
    return {"submitted": False, "format": None, "attempts": attempts}


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


async def observe_after(ctx: CheckContext) -> dict:
    if not ctx.data["evidence"].get("submitted"):
        return {"after": None, "after_history": []}
    status = ctx.data["before"]["status"]
    detail, history = await poll_dispute(
        ctx,
        ctx.data["dispute_id"],
        done=lambda detail: detail.get("status") != status,
        step="after_evidence",
        max_polls=AFTER_MAX_POLLS,
    )
    return {"after": summarize(detail), "after_history": history}


async def findings(ctx: CheckContext) -> dict:
    evidence = ctx.data["evidence"]
    if evidence.get("submitted"):
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
            "evidence_accepted_stage": evidence.get("stage") if evidence.get("submitted") else None,
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
