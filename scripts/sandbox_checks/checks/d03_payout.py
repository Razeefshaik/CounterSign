"""D3: a single $1 payout to Maker A, with batch and item GET evidence."""

from ..runner import Step
from . import payout_common as common

AMOUNT_MINOR = 100


async def load_inputs(ctx):
    if not ctx.client.settings.maker_a_email:
        return {"blocked": "SBX_MAKER_A_EMAIL is not set"}
    if "baseline_balance" not in ctx.data:
        ctx.checkpoint(baseline_balance=await common.balance_snapshot(ctx, step="baseline_balance"))


async def submit(ctx):
    if ctx.data.get("blocked"):
        return
    if "payout" not in ctx.data:
        ctx.checkpoint(
            payout=await common.create_payout(
                ctx, "maker_a", ctx.client.settings.maker_a_email, AMOUNT_MINOR
            )
        )


async def inspect(ctx):
    payout = dict(ctx.data.get("payout") or {})
    if not payout.get("batch_id"):
        return
    result = await common.poll_payout(ctx, "maker_a", payout["batch_id"])
    payout.update(result)
    payout["item_id"] = result["item"].get("payout_item_id")
    payout["item_status"] = result["item"].get("transaction_status")
    return {"payout": payout}


async def observe(ctx):
    payout = ctx.data.get("payout") or {}
    if not payout.get("batch_id"):
        return {"balance": None}
    return {
        "balance": await common.observe_balance(
            ctx, "payout", ctx.data["baseline_balance"], -AMOUNT_MINOR
        )
    }


async def findings(ctx):
    payout = ctx.data.get("payout") or {}
    verdict = "BLOCKED"
    if payout.get("item_status"):
        verdict = "CONFIRMED" if payout["item_status"] == "SUCCESS" else "DIFFERENT"
    return common.findings(
        ctx,
        "D3",
        verdict,
        payout=payout,
        balance=ctx.data.get("balance"),
        create_had_item_ids=any(
            item.get("payout_item_id")
            for item in (payout.get("create_body") or {}).get("items") or []
        ),
        webhooks=common.webhooks(ctx, [payout.get("batch_id"), payout.get("item_id")]),
        blocked=ctx.data.get("blocked"),
        notes=["The smallest check amount is $1; this is not the maker's production share."],
    )


def get_steps():
    return [
        Step("load_inputs", load_inputs),
        Step("submit", submit),
        Step("inspect", inspect),
        Step("observe", observe),
        Step("findings", findings),
    ]
