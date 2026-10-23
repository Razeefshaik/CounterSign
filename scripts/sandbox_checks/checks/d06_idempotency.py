"""D6: deliberate payout idempotency experiments, always preserving each saved body/key."""

import asyncio
import copy

import httpx

from ..runner import Step
from . import payout_common as common

AMOUNT_MINOR = 100


async def load_inputs(ctx):
    if not ctx.client.settings.maker_b_email:
        return {"blocked": "SBX_MAKER_B_EMAIL is not set"}
    if "baseline_balance" not in ctx.data:
        ctx.checkpoint(baseline_balance=await common.balance_snapshot(ctx, step="baseline_balance"))


def prepare_base(ctx, name):
    saved = (ctx.data.get("writes") or {}).get(name)
    body = (
        common.restore(ctx, saved["body"])
        if saved
        else common.payout_body(ctx, ctx.client.settings.maker_b_email, AMOUNT_MINOR)
    )
    return common.prepare(
        ctx,
        name,
        "/v1/payments/payouts",
        body,
        request_id=body["sender_batch_header"]["sender_batch_id"],
    )


async def seed(ctx, name):
    prepare_base(ctx, name)
    result = await common.send(ctx, name)
    if result["state"] == "UNKNOWN":
        # D6 deliberately exercises identical replays, including recovery after an interruption.
        result = await common.send(ctx, name, replay=True)
    return result


def response_batch(result):
    body = result.get("response") or {}
    return common.batch_id(body) or common.batch_link(body)


async def confirm(ctx, name, results):
    batches = sorted({value for value in map(response_batch, results) if value})
    observations = {}
    for batch_id in batches:
        observations[batch_id] = await common.poll_payout(ctx, name, batch_id)
    verified = bool(observations) and all(
        value["item_count"] == 1 and value["item"].get("transaction_status") == "SUCCESS"
        for value in observations.values()
    )
    return {"batch_ids": batches, "observations": observations, "verified_single_item": verified}


def save(ctx, label, data):
    experiments = dict(ctx.data.get("experiments") or {})
    experiments[label] = data
    ctx.checkpoint(experiments=experiments)
    return data


async def exact_replay(ctx):
    if ctx.data.get("blocked"):
        return
    first = await seed(ctx, "a_base")
    original = ctx.data["writes"]["a_base"]
    common.prepare(
        ctx,
        "a_replay",
        original["path"],
        common.restore(ctx, original["body"]),
        request_id=original["request_id"],
    )
    replay = await common.send(ctx, "a_replay")
    proof = await confirm(ctx, "a_base", [first, replay])
    same = response_batch(first) is not None and response_batch(first) == response_batch(replay)
    save(
        ctx,
        "a",
        {
            "first": first,
            "replay": replay,
            **proof,
            "same_batch": same,
            "verdict": "CONFIRMED"
            if same and proof["verified_single_item"]
            else "BLOCKED"
            if not proof["verified_single_item"]
            else "DIFFERENT",
        },
    )


async def new_request_id(ctx):
    if ctx.data.get("blocked"):
        return
    first = await seed(ctx, "b_base")
    original = ctx.data["writes"]["b_base"]
    common.prepare(ctx, "b_new_request", original["path"], common.restore(ctx, original["body"]))
    second = await common.send(ctx, "b_new_request")
    proof = await confirm(ctx, "b_base", [first, second])
    linked = common.batch_link(second.get("response") or {})
    same = bool(response_batch(first)) and linked == response_batch(first)
    code = second.get("status")
    rejected = isinstance(code, int) and 400 <= code < 500
    verdict = (
        "CONFIRMED"
        if rejected and same and proof["verified_single_item"]
        else "BLOCKED"
        if not proof["verified_single_item"]
        else "DIFFERENT"
    )
    save(
        ctx,
        "b",
        {
            "first": first,
            "new_request": second,
            **proof,
            "error_name": (second.get("response") or {}).get("name"),
            "link_points_to_original": same,
            "verdict": verdict,
        },
    )


async def changed_body(ctx):
    if ctx.data.get("blocked"):
        return
    first = await seed(ctx, "c_base")
    original = ctx.data["writes"]["c_base"]
    body = copy.deepcopy(common.restore(ctx, original["body"]))
    body["items"][0]["note"] = "Staged sandbox action: deliberately changed note."
    common.prepare(ctx, "c_changed_body", original["path"], body, request_id=original["request_id"])
    changed = await common.send(ctx, "c_changed_body")
    proof = await confirm(ctx, "c_base", [first, changed])
    notes = {
        batch: (entry["item"].get("payout_item") or {}).get("note")
        for batch, entry in proof["observations"].items()
    }
    original_note = original["body"]["items"][0]["note"]
    winner = (
        "original"
        if notes and all(note == original_note for note in notes.values())
        else "changed"
        if notes and all(note == body["items"][0]["note"] for note in notes.values())
        else "unknown"
    )
    verdict = (
        "BLOCKED"
        if not proof["verified_single_item"] or winner == "unknown"
        else "CONFIRMED"
        if len(proof["batch_ids"]) == 1 and winner == "original"
        else "DIFFERENT"
    )
    save(
        ctx,
        "c",
        {
            "first": first,
            "changed_body": changed,
            **proof,
            "notes_by_batch": notes,
            "winning_body": winner,
            "verdict": verdict,
        },
    )


async def concurrent(ctx):
    if ctx.data.get("blocked"):
        return
    prepare_base(ctx, "d_concurrent")
    saved = (ctx.data.get("experiments") or {}).get("d") or {}
    if not saved.get("started"):
        save(ctx, "d", {"started": True})
        # Both POSTs use the SAME key and body. replay=True bypasses the normal once-only guard.
        results = await asyncio.gather(
            common.send(ctx, "d_concurrent", replay=True),
            common.send(ctx, "d_concurrent", replay=True),
        )
        saved = save(ctx, "d", {"started": True, "responses": results})
    elif not saved.get("responses"):
        # Don't repeat a pair after interruption: reconcile the saved attempt once.
        result = await common.send(ctx, "d_concurrent", replay=True)
        saved = save(ctx, "d", {"started": True, "responses": [result], "interrupted_pair": True})
    proof = await confirm(ctx, "d_concurrent", saved["responses"])
    verdict = (
        "BLOCKED"
        if not proof["verified_single_item"] or saved.get("interrupted_pair")
        else "CONFIRMED"
        if len(proof["batch_ids"]) == 1
        else "DIFFERENT"
    )
    save(ctx, "d", {**saved, **proof, "verdict": verdict})


async def lost_response(ctx):
    if ctx.data.get("blocked"):
        return
    saved = (ctx.data.get("experiments") or {}).get("e") or {}
    if "baseline" not in saved:
        saved = save(
            ctx, "e", {"baseline": await common.balance_snapshot(ctx, step="lost_baseline")}
        )
    prepare_base(ctx, "e_lost_response")
    if "initial" not in saved:
        initial = await common.send(ctx, "e_lost_response", timeout=httpx.Timeout(30, read=0.05))
        saved = save(ctx, "e", {**saved, "initial": initial})
    original = ctx.data["writes"]["e_lost_response"]
    common.prepare(
        ctx,
        "e_replay",
        original["path"],
        common.restore(ctx, original["body"]),
        request_id=original["request_id"],
    )
    replay = await common.send(ctx, "e_replay")
    if replay["state"] == "UNKNOWN":
        replay = await common.send(ctx, "e_replay", replay=True)
    proof = await confirm(ctx, "e_lost_response", [saved["initial"], replay])
    balance = await common.observe_balance(ctx, "lost_response", saved["baseline"], -AMOUNT_MINOR)
    ambiguous = saved["initial"]["state"] == "UNKNOWN"
    verdict = (
        "CONFIRMED"
        if ambiguous
        and proof["verified_single_item"]
        and len(proof["batch_ids"]) == 1
        and balance["first_reflected_at"]
        else "BLOCKED"
    )
    save(
        ctx,
        "e",
        {
            **saved,
            "replay": replay,
            **proof,
            "balance": balance,
            "initial_unknown": ambiguous,
            "verdict": verdict,
            "notes": "If the 50 ms call returns normally, a lost response was not reproduced.",
        },
    )


async def findings(ctx):
    experiments = ctx.data.get("experiments") or {}
    verdicts = [entry.get("verdict", "BLOCKED") for entry in experiments.values()]
    verdict = (
        "BLOCKED"
        if len(verdicts) != 5 or "BLOCKED" in verdicts
        else "DIFFERENT"
        if "DIFFERENT" in verdicts
        else "CONFIRMED"
    )
    after = await common.balance_snapshot(ctx, step="final_balance")
    return common.findings(
        ctx,
        "D6",
        verdict,
        experiments=experiments,
        baseline_balance=ctx.data.get("baseline_balance"),
        final_balance=after,
        blocked=ctx.data.get("blocked"),
        notes=[
            "Each scenario uses fresh keys for one $1 payment; every replay reuses its saved keys.",
            "Changed-body testing changes only the note, not amount or recipient.",
            "The 50 ms timeout is a transport setting, not a simulated successful UNKNOWN fixture.",
        ],
    )


def get_steps():
    return [
        Step("load_inputs", load_inputs),
        Step("exact_replay", exact_replay),
        Step("new_request_id", new_request_id),
        Step("changed_body", changed_body),
        Step("concurrent", concurrent),
        Step("lost_response", lost_response),
        Step("findings", findings),
    ]
