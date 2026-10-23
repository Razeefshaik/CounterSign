"""D2: three buyer-approved captures and Orders v2 tracking, with resumable writes."""

from .. import timeline
from ..runner import Step
from . import payout_common as common

ORDERS = {"inr_400": 40000, "snad_60": 6000, "spare_25": 2500}


async def load_inputs(ctx):
    if "baseline_balance" not in ctx.data:
        ctx.checkpoint(baseline_balance=await common.balance_snapshot(ctx, step="baseline_balance"))


async def create_orders(ctx):
    orders = dict(ctx.data.get("orders") or {})
    for role, amount in ORDERS.items():
        if role in orders:
            continue
        result = await common.idempotent_write(
            ctx,
            f"create_{role}",
            "/v2/checkout/orders",
            {
                "intent": "CAPTURE",
                "purchase_units": [
                    {
                        "reference_id": role,
                        "description": "Staged sandbox action: CounterSign check.",
                        "amount": {"currency_code": "USD", "value": common.dollars(amount)},
                    }
                ],
            },
        )
        body = result.get("response") or {}
        if result["state"] != "SUCCEEDED" or not body.get("id"):
            raise common.PayoutCheckError("Order creation did not return an order ID.")
        links = body.get("links") or []
        orders[role] = {
            "order_id": common.resource_id(body["id"]),
            "status": body.get("status"),
            "approval_url": next(
                (
                    link.get("href")
                    for link in links
                    if link.get("rel") in ("approve", "payer-action")
                ),
                None,
            ),
        }
        ctx.checkpoint(orders=orders)


async def approve_orders(ctx):
    orders = dict(ctx.data["orders"])
    for role, order in orders.items():
        if order.get("approved_at"):
            continue
        url = order.get("approval_url")
        if not url or not url.startswith("https://www.sandbox.paypal.com/"):
            raise common.PayoutCheckError("No sandbox approval URL was returned.")
        await ctx.manual.confirm(
            f"D2 staged sandbox action: log in as buyer-1 and approve the ${common.dollars(ORDERS[role])} "
            f"{role} order at {url}."
        )
        response = await ctx.client.request(
            "GET", f"/v2/checkout/orders/{order['order_id']}", step=f"approved_{role}"
        )
        if response.status_code != 200 or common.payload(response).get("status") not in (
            "APPROVED",
            "COMPLETED",
        ):
            raise common.PayoutCheckError("Buyer approval is not yet confirmed by GET.")
        order["approved_at"] = common.now(ctx)
        ctx.checkpoint(orders=orders)


def captures_in(body):
    return [
        capture
        for unit in body.get("purchase_units") or []
        for capture in (unit.get("payments") or {}).get("captures") or []
    ]


async def capture_orders(ctx):
    captures = dict(ctx.data.get("captures") or {})
    for role, order in ctx.data["orders"].items():
        if role in captures:
            continue
        name = f"capture_{role}"
        # An accepted capture can be recovered by GET without repeating the write.
        response = await ctx.client.request(
            "GET", f"/v2/checkout/orders/{order['order_id']}", step=f"before_{name}"
        )
        found = captures_in(common.payload(response)) if response.status_code == 200 else []
        if not found:
            result = await common.idempotent_write(
                ctx, name, f"/v2/checkout/orders/{order['order_id']}/capture", {}
            )
            found = captures_in(result.get("response") or {})
        if len(found) != 1 or not found[0].get("id"):
            raise common.PayoutCheckError(
                "Capture response has no unique capture ID; inspect evidence."
            )
        capture = found[0]
        capture_id = common.resource_id(capture["id"])
        if not capture.get("seller_receivable_breakdown"):
            response = await ctx.client.request(
                "GET", f"/v2/payments/captures/{capture_id}", step=f"detail_{name}"
            )
            if response.status_code == 200:
                capture = common.payload(response)
        breakdown = capture.get("seller_receivable_breakdown") or {}
        net = breakdown.get("net_amount") or {}
        captures[role] = {
            "capture_id": capture_id,
            "status": capture.get("status"),
            "seller_receivable_breakdown": breakdown,
            "net_minor": common.cents(net.get("value")),
            "currency": net.get("currency_code"),
            "observed_at": common.now(ctx),
            "paypal_time": capture.get("update_time") or capture.get("create_time"),
        }
        ctx.checkpoint(captures=captures)
    # Also executes on resume after a capture was checkpointed but before its event was saved.
    for capture in captures.values():
        if capture["status"] == "COMPLETED":
            timeline.record(
                ctx,
                "CAPTURE_COMPLETED",
                resource_id=capture["capture_id"],
                amount_minor=capture["net_minor"],
                currency=capture["currency"],
                at=capture["observed_at"],
                paypal_time=capture.get("paypal_time"),
            )
    return {"captures": captures}


async def add_tracking(ctx):
    tracking = ctx.data.get("tracking") or {}
    if "inr_400" not in tracking:
        tracking["inr_400"] = {
            "carrier": await ctx.manual.ask_id(
                "D2 staged sandbox action: enter the test carrier code, e.g. USPS."
            ),
            "tracking_number": await ctx.manual.ask_id(
                "Enter the test tracking number for the $400 order."
            ),
        }
        ctx.checkpoint(tracking=tracking)
    name = "track_inr_400"
    order_id = ctx.data["orders"]["inr_400"]["order_id"]
    body = {
        "capture_id": ctx.data["captures"]["inr_400"]["capture_id"],
        **tracking["inr_400"],
        "notify_payer": False,
    }
    entry = common.prepare(ctx, name, f"/v2/checkout/orders/{order_id}/track", body)
    # Tracking POST has no assumed idempotency guarantee. Unknown remains blocked.
    entry = await common.send(ctx, name)
    if entry["state"] == "UNKNOWN":
        ctx.note("D2 tracking outcome UNKNOWN; no second tracking POST will be sent.")
    return {"tracking_write_state": entry["state"], "tracking_status": entry.get("status")}


async def inspect_tracking(ctx):
    response = await ctx.client.request(
        "GET",
        "/v1/shipping/trackers",
        step="get_tracking",
        params={"transaction_id": ctx.data["captures"]["inr_400"]["capture_id"]},
    )
    return {"tracking_read_status": response.status_code, "tracking_read": common.payload(response)}


async def observe(ctx):
    net = [entry.get("net_minor") for entry in ctx.data["captures"].values()]
    delta = sum(net) if all(value is not None for value in net) else None
    balance = await common.observe_balance(ctx, "money_in", ctx.data["baseline_balance"], delta)
    return {"balance": balance}


async def findings(ctx):
    completed = all(entry.get("status") == "COMPLETED" for entry in ctx.data["captures"].values())
    verdict = (
        "CONFIRMED" if completed and ctx.data["tracking_write_state"] == "SUCCEEDED" else "BLOCKED"
    )
    return common.findings(
        ctx,
        "D2",
        verdict,
        captures=ctx.data["captures"],
        tracking=ctx.data["tracking"],
        tracking_read=ctx.data["tracking_read"],
        tracking_read_status=ctx.data["tracking_read_status"],
        balance=ctx.data["balance"],
        webhooks=common.webhooks(
            ctx, [entry["capture_id"] for entry in ctx.data["captures"].values()]
        ),
        notes=[
            "Tracking is merchant-supplied test data, not independent carrier proof.",
            "Missing webhooks and balance lag are analysed by D11 and D12.",
        ],
    )


def get_steps():
    return [
        Step("load_inputs", load_inputs),
        Step("create_orders", create_orders),
        Step("approve_orders", approve_orders),
        Step("capture_orders", capture_orders),
        Step("add_tracking", add_tracking),
        Step("inspect_tracking", inspect_tracking),
        Step("observe", observe),
        Step("findings", findings),
    ]
