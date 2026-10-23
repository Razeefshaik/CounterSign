"""D4: three unregistered recipients and the observed available/withheld effects."""

from ..runner import Step
from . import payout_common as common

AMOUNT_MINOR = 100
ROLES = ("unreg_1", "unreg_2", "unreg_3")


async def load_inputs(ctx):
    if len(ctx.client.settings.unregistered_emails) < 3:
        return {"blocked": "SBX_UNREG_EMAILS must contain three never-registered addresses"}
    if "baseline_balance" not in ctx.data:
        ctx.checkpoint(baseline_balance=await common.balance_snapshot(ctx, step="baseline_balance"))


async def submit(ctx):
    if ctx.data.get("blocked"):
        return
    payouts = dict(ctx.data.get("payouts") or {})
    for role, receiver in zip(ROLES, ctx.client.settings.unregistered_emails, strict=False):
        if role not in payouts:
            payouts[role] = await common.create_payout(ctx, role, receiver, AMOUNT_MINOR)
            ctx.checkpoint(payouts=payouts)


async def inspect(ctx):
    payouts = dict(ctx.data.get("payouts") or {})
    for role, payout in payouts.items():
        if payout.get("batch_id") and not payout.get("item_id"):
            payout.update(await common.poll_payout(ctx, role, payout["batch_id"]))
            payout["item_id"] = payout["item"].get("payout_item_id")
            payout["item_status"] = payout["item"].get("transaction_status")
            ctx.checkpoint(payouts=payouts)
    return {"payouts": payouts}


async def observe(ctx):
    payouts = ctx.data.get("payouts") or {}
    if not payouts:
        return {"balance": None}
    amount = sum(p["amount_minor"] for p in payouts.values() if p.get("batch_id"))
    return {
        "balance": await common.observe_balance(
            ctx, "unclaimed", ctx.data["baseline_balance"], -amount
        )
    }


def balance_effect(balance):
    samples = balance.get("observations") or []
    baseline = balance.get("baseline") or {}
    last = samples[-1] if samples else {}
    result = {}
    for field in ("available", "withheld"):
        before = common.cents(baseline.get(field)) if baseline.get("status") == 200 else None
        after = common.cents(last.get(field)) if last.get("status") == 200 else None
        result[field + "_delta_minor"] = (
            after - before if before is not None and after is not None else None
        )
    result["available_dropped"] = (
        result["available_delta_minor"] < 0 if result["available_delta_minor"] is not None else None
    )
    result["withheld_changed"] = (
        result["withheld_delta_minor"] != 0 if result["withheld_delta_minor"] is not None else None
    )
    return result


async def findings(ctx):
    payouts = ctx.data.get("payouts") or {}
    statuses = [payout.get("item_status") for payout in payouts.values()]
    unclaimed = (
        "CONFIRMED"
        if len(statuses) == 3 and all(s == "UNCLAIMED" for s in statuses)
        else "DIFFERENT"
        if any(statuses)
        else "BLOCKED"
    )
    effect = balance_effect(ctx.data.get("balance") or {})
    balance_verdict = (
        "BLOCKED"
        if any(effect[key] is None for key in ("available_delta_minor", "withheld_delta_minor"))
        else "CONFIRMED"
    )
    verdict = "BLOCKED" if "BLOCKED" in (unclaimed, balance_verdict) else unclaimed
    ids = [p.get(key) for p in payouts.values() for key in ("batch_id", "item_id")]
    return common.findings(
        ctx,
        "D4",
        verdict,
        payouts=payouts,
        unclaimed_verdict=unclaimed,
        balance_verdict=balance_verdict,
        balance_effect=effect,
        balance=ctx.data.get("balance"),
        webhooks=common.webhooks(ctx, ids),
        blocked=ctx.data.get("blocked"),
        notes=[
            "Three $1 payouts minimise the experiment amount.",
            "unreg_2 remains uncancelled; D5 monitors it until the day-one deadline.",
            "Balance deltas are observed snapshots; D12 checks freshness.",
        ],
    )


def get_steps():
    return [
        Step("load_inputs", load_inputs),
        Step("submit", submit),
        Step("inspect", inspect),
        Step("observe", observe),
        Step("findings", findings),
    ]
