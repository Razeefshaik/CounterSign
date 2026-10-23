"""D5: cancel once, GET-confirm the return, negative cases and an uncancelled control."""

import json
from datetime import UTC, datetime

from ..runner import Step
from . import payout_common as common

MONITOR_DEADLINE = datetime(2026, 10, 25, 18, 29, 59, tzinfo=UTC)  # Oct 25 23:59:59 IST
MONITOR_INTERVAL = 300


async def read_item(ctx, item_id, step):
    response = await ctx.client.request(
        "GET", f"/v1/payments/payouts-item/{common.resource_id(item_id)}", step=step
    )
    return common.payload(response) if response.status_code == 200 else {}


async def poll_item(ctx, item_id, name, targets):
    histories = dict(ctx.data.get("item_observations") or {})
    history = list(histories.get(name) or [])
    detail = {}
    for poll in range(common.MAX_POLLS):
        if poll:
            await ctx.sleep(common.POLL_SECONDS)
        detail = await read_item(ctx, item_id, name)
        history.append(
            {"at": common.now(ctx), "status": detail.get("transaction_status"), "item_id": item_id}
        )
        histories[name] = history
        ctx.checkpoint(item_observations=histories)
        common.item_event(ctx, detail)
        if detail.get("transaction_status") in targets:
            break
    return detail


async def load_inputs(ctx):
    d3, d4 = ctx.load_check_data("D3"), ctx.load_check_data("D4")
    payouts = d4.get("payouts") or {}
    inputs = {
        role: (payouts.get(role) or {}).get("item_id") for role in ("unreg_1", "unreg_2", "unreg_3")
    }
    inputs["success"] = (d3.get("payout") or {}).get("item_id")
    if not all(inputs.values()):
        return {"blocked": "D3 SUCCESS and all three D4 payout item IDs are required"}
    ctx.checkpoint(inputs=inputs)
    if "baseline_balance" not in ctx.data:
        ctx.checkpoint(baseline_balance=await common.balance_snapshot(ctx, step="baseline_balance"))


async def cancel(ctx):
    if ctx.data.get("blocked"):
        return
    item_id = ctx.data["inputs"]["unreg_1"]
    before = await read_item(ctx, item_id, "before_cancel")
    entry = (ctx.data.get("writes") or {}).get("cancel_unreg_1")
    if before.get("transaction_status") == "UNCLAIMED" and entry is None:
        common.prepare(ctx, "cancel_unreg_1", f"/v1/payments/payouts-item/{item_id}/cancel", {})
        entry = await common.send(ctx, "cancel_unreg_1")
    elif entry is None:
        return {"cancel": {"blocked": "Initial item GET is not UNCLAIMED", "before": before}}
    # An UNKNOWN cancel is never automatically repeated. GET reconciles its outcome.
    final = await poll_item(ctx, item_id, "cancel_get", {"RETURNED", "SUCCESS"})
    return {
        "cancel": {
            "before": before,
            "response_status": entry.get("status"),
            "response_transaction_status": (entry.get("response") or {}).get("transaction_status"),
            "write_state": entry["state"],
            "final": final,
        }
    }


async def negative_cases(ctx):
    if ctx.data.get("blocked"):
        return
    cases = dict(ctx.data.get("negative_cases") or {})
    targets = [
        ("cancel_again", ctx.data["inputs"]["unreg_1"], "RETURNED"),
        ("cancel_success", ctx.data["inputs"]["success"], "SUCCESS"),
    ]
    for name, item_id, required in targets:
        if name in cases:
            continue
        before = await read_item(ctx, item_id, f"before_{name}")
        if before.get("transaction_status") != required:
            cases[name] = {"blocked": f"GET must confirm {required} before this negative test"}
        else:
            common.prepare(ctx, name, f"/v1/payments/payouts-item/{item_id}/cancel", {})
            result = await common.send(ctx, name)
            cases[name] = {
                "status": result.get("status"),
                "state": result["state"],
                "error_name": (result.get("response") or {}).get("name"),
            }
        ctx.checkpoint(negative_cases=cases)
    return {"negative_cases": cases}


async def observe(ctx):
    if ctx.data.get("blocked"):
        return {"balance": None}
    final = (ctx.data.get("cancel") or {}).get("final") or {}
    returned = final.get("transaction_status") == "RETURNED"
    amount = common.cents(((final.get("payout_item") or {}).get("amount") or {}).get("value"))
    return {
        "balance": await common.observe_balance(
            ctx, "cancel", ctx.data["baseline_balance"], amount if returned else None
        )
    }


async def optional_claim(ctx):
    saved = ctx.data.get("claim_race") or {}
    if ctx.data.get("blocked") or saved.get("skipped") or saved.get("final"):
        return
    if not saved.get("requested"):
        choice = await ctx.manual.ask_id(
            "D5 optional staged sandbox action: create a sandbox personal account at unreg-3 "
            "to test claiming its payout? Type yes or no. It cannot remain an unregistered address afterwards."
        )
        if choice.lower() != "yes":
            return {"claim_race": {"skipped": True}}
        ctx.checkpoint(claim_race={"requested": True})
    await ctx.manual.confirm(
        "D5 staged sandbox action: create the unreg-3 sandbox personal account in the "
        "developer dashboard, then open its sandbox account and claim the payment if offered."
    )
    detail = await poll_item(
        ctx, ctx.data["inputs"]["unreg_3"], "claim_race", common.TERMINAL - {"UNCLAIMED"}
    )
    return {"claim_race": {"requested": True, "final": detail}}


async def findings(ctx):
    result = ctx.data.get("cancel") or {}
    state = (result.get("final") or {}).get("transaction_status")
    negatives = ctx.data.get("negative_cases") or {}
    checked = len(negatives) == 2 and all(
        isinstance(case.get("status"), int) and 400 <= case["status"] < 500
        for case in negatives.values()
    )
    verdict = (
        "CONFIRMED" if state == "RETURNED" and checked else "DIFFERENT" if state else "BLOCKED"
    )
    return common.findings(
        ctx,
        "D5",
        verdict,
        cancel=result,
        negative_cases=negatives,
        balance=ctx.data.get("balance"),
        claim_race=ctx.data.get("claim_race"),
        webhooks=common.webhooks(ctx, list((ctx.data.get("inputs") or {}).values())),
        monitor_deadline=MONITOR_DEADLINE.isoformat(),
        blocked=ctx.data.get("blocked"),
        notes=[
            "Initial findings are saved before the long-running unreg-2 monitor.",
            "RETURNED must be observed by GET; a cancel response or webhook is insufficient.",
        ],
    )


async def monitor_control(ctx):
    if ctx.data.get("blocked"):
        return
    samples = list(ctx.data.get("control_observations") or [])
    while True:
        if samples:
            if ctx.clock() >= MONITOR_DEADLINE or samples[-1].get("status") in common.TERMINAL - {
                "UNCLAIMED"
            }:
                break
            remaining = max(1, int((MONITOR_DEADLINE - ctx.clock()).total_seconds()) + 1)
            await ctx.sleep(min(MONITOR_INTERVAL, remaining))
        item = await read_item(ctx, ctx.data["inputs"]["unreg_2"], "uncancelled_control")
        sample = {
            "at": common.now(ctx),
            "item_id": ctx.data["inputs"]["unreg_2"],
            "status": item.get("transaction_status"),
        }
        samples.append(sample)
        ctx.checkpoint(control_observations=samples)
        common.item_event(ctx, item)
        if ctx.clock() >= MONITOR_DEADLINE:
            break
    # Refresh the public finding with the finished control observation.
    await findings(ctx)
    finding_path = ctx.check_dir / "findings.json"
    finding = json.loads(finding_path.read_text(encoding="utf-8"))
    finding["control_observations"] = samples
    ctx.write_json(finding_path, finding)


def get_steps():
    return [
        Step("load_inputs", load_inputs),
        Step("cancel", cancel),
        Step("negative_cases", negative_cases),
        Step("observe", observe),
        Step("optional_claim", optional_claim),
        Step("findings", findings),
        Step("monitor_control", monitor_control),
    ]
