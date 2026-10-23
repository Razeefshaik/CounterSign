"""C05 helpers: durable requests, integer USD amounts and observed payout states.

Saved bodies contain public email aliases. The hash binds them to the exact private
body sent on the wire; restoring aliases with changed settings refuses the write.
"""

import copy
import hashlib
import json
import re
import secrets
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit
from uuid import UUID

from .. import timeline
from ..client import SandboxRequestError
from .d08_dispute_create import (
    balance_snapshot,
    now,
    payload,
    resource_id,
    write_findings,
)

POLL_SECONDS = 30
MAX_POLLS = 60
BALANCE_SECONDS = 300
BALANCE_POLLS = 37
TERMINAL = {"SUCCESS", "FAILED", "UNCLAIMED", "RETURNED", "REFUNDED", "REVERSED"}
ITEM_EVENTS = {
    "SUCCESS": "PAYOUT_ITEM_SUCCESS",
    "UNCLAIMED": "PAYOUT_ITEM_UNCLAIMED",
    "FAILED": "PAYOUT_ITEM_FAILED",
    "RETURNED": "PAYOUT_ITEM_RETURNED",
}


class PayoutCheckError(RuntimeError):
    """Safe, names-only diagnostic; the runner saves progress before stopping."""


def uuid7(clock) -> UUID:
    """RFC 9562: 48-bit Unix milliseconds, version 7, variant 10, 74 random bits."""
    delta = clock().astimezone(UTC) - datetime(1970, 1, 1, tzinfo=UTC)
    millis = delta.days * 86400000 + delta.seconds * 1000 + delta.microseconds // 1000
    if not 0 <= millis < 1 << 48:
        raise ValueError("Clock outside UUIDv7 timestamp range.")
    return UUID(
        int=(millis << 80)
        | (7 << 76)
        | (secrets.randbits(12) << 64)
        | (2 << 62)
        | secrets.randbits(62)
    )


def new_key(ctx, attempt=1):
    return f"cs-{uuid7(ctx.clock)}-a{attempt}"


def dollars(minor: int) -> str:
    if type(minor) is not int or minor < 0:
        raise ValueError("USD money must be nonnegative integer minor units.")
    return f"{minor // 100}.{minor % 100:02d}"


def cents(value) -> int | None:
    if not isinstance(value, str):
        return None
    try:
        number = Decimal(value) * 100
        if number.is_finite() and number == number.to_integral_value():
            return int(number)
    except InvalidOperation:
        pass
    return None


def digest(body):
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def restore(ctx, value):
    aliases = {alias: email for email, alias in ctx.client.settings.email_aliases.items()}
    if isinstance(value, dict):
        return {key: restore(ctx, item) for key, item in value.items()}
    if isinstance(value, list):
        return [restore(ctx, item) for item in value]
    return aliases.get(value, value) if isinstance(value, str) else value


def prepare(ctx, name, path, body, *, request_id=None):
    writes = copy.deepcopy(ctx.data.get("writes") or {})
    if name not in writes:
        writes[name] = {
            "path": path,
            "body": ctx.recorder.redactor.redact(body),
            "body_sha256": digest(body),
            "request_id": request_id or new_key(ctx),
            "state": "PREPARED",
        }
        ctx.checkpoint(writes=writes)
    entry = writes[name]
    if entry["path"] != path or digest(restore(ctx, entry["body"])) != entry["body_sha256"]:
        raise PayoutCheckError("Saved request body or runtime settings changed; write refused.")
    return entry


async def send(ctx, name, *, replay=False, timeout=None):
    writes = copy.deepcopy(ctx.data["writes"])
    entry = writes[name]
    body = restore(ctx, entry["body"])
    if digest(body) != entry["body_sha256"]:
        raise PayoutCheckError("Saved request body or runtime settings changed; write refused.")
    if entry["state"] != "PREPARED" and not replay:
        return entry
    entry.update(state="UNKNOWN", sent_at=now(ctx))
    ctx.checkpoint(writes=writes)  # durable BEFORE the POST, including deliberate replays
    try:
        response = await ctx.client.request(
            "POST",
            entry["path"],
            step=name,
            request_id=entry["request_id"],
            headers={"Prefer": "return=representation"}
            if entry["path"].startswith("/v2/checkout/orders")
            else None,
            json=body,
            timeout=timeout,
        )
    except SandboxRequestError:
        entry["error"] = "SandboxRequestError"
    else:
        code = response.status_code
        entry.update(
            status=code,
            response=ctx.recorder.redactor.redact(payload(response)),
            state="SUCCEEDED"
            if 200 <= code < 300
            else "REJECTED"
            if 400 <= code < 500
            else "UNKNOWN",
        )
    # Other tasks can checkpoint while HTTP is in flight; don't overwrite their writes.
    writes = copy.deepcopy(ctx.data["writes"])
    writes[name] = entry
    ctx.checkpoint(writes=writes)
    return entry


async def idempotent_write(ctx, name, path, body, *, request_id=None):
    entry = prepare(ctx, name, path, body, request_id=request_id)
    replay = entry["state"] == "UNKNOWN"
    if replay:
        await ctx.manual.confirm(
            f"{name}: the saved write is UNKNOWN. This staged sandbox action repeats the "
            "identical saved body and request ID to reconcile it; no new keys are created."
        )
    entry = await send(ctx, name, replay=replay)
    if entry["state"] == "UNKNOWN":
        raise PayoutCheckError("Write outcome UNKNOWN; resume with saved keys to reconcile.")
    return entry


def batch_id(body):
    return (body.get("batch_header") or {}).get("payout_batch_id")


def batch_link(body):
    """Only follow a payout-batch GET link within the sandbox API host."""
    for link in body.get("links") or []:
        if not isinstance(link, dict) or link.get("method", "GET") != "GET":
            continue
        parsed = urlsplit(link.get("href", ""))
        if parsed.netloc and (
            parsed.scheme != "https" or parsed.netloc != "api-m.sandbox.paypal.com"
        ):
            continue
        if re.fullmatch(r"/v1/payments/payouts/[A-Za-z0-9_-]+", parsed.path):
            return parsed.path.rsplit("/", 1)[-1]
    return None


def payout_body(
    ctx, receiver, amount, *, key=None, note="Staged sandbox action: CounterSign check."
):
    key = key or new_key(ctx)
    return {
        "sender_batch_header": {
            "sender_batch_id": key,
            "email_subject": "CounterSign sandbox check",
        },
        "items": [
            {
                "recipient_type": "EMAIL",
                "receiver": receiver,
                "amount": {"value": dollars(amount), "currency": "USD"},
                "sender_item_id": key,
                "note": note,
            }
        ],
    }


async def create_payout(ctx, name, receiver, amount):
    saved = (ctx.data.get("writes") or {}).get(name)
    body = restore(ctx, saved["body"]) if saved else payout_body(ctx, receiver, amount)
    result = await idempotent_write(
        ctx,
        name,
        "/v1/payments/payouts",
        body,
        request_id=body["sender_batch_header"]["sender_batch_id"],
    )
    response = result.get("response") or {}
    found = batch_id(response) or batch_link(response)
    if found:
        found = resource_id(found)
    if found and result["state"] == "SUCCEEDED":
        timeline.record(
            ctx,
            "PAYOUT_SUBMITTED",
            resource_id=found,
            amount_minor=amount,
            currency="USD",
            paypal_time=(response.get("batch_header") or {}).get("time_created"),
        )
    return {
        "batch_id": found,
        "create_status": result.get("status"),
        "create_body": response,
        "amount_minor": amount,
        "write_name": name,
    }


def item_event(ctx, item):
    event = ITEM_EVENTS.get(item.get("transaction_status"))
    if event and item.get("payout_item_id"):
        amount = (item.get("payout_item") or {}).get("amount") or {}
        timeline.record(
            ctx,
            event,
            resource_id=item["payout_item_id"],
            amount_minor=cents(amount.get("value")),
            currency=amount.get("currency"),
            paypal_time=item.get("time_processed"),
        )


def matching_item(ctx, item, expected):
    actual = item.get("payout_item") or {}
    return all(
        actual.get(key) == expected.get(key) for key in ("receiver", "amount", "sender_item_id")
    )


async def poll_payout(ctx, name, batch, *, target=TERMINAL):
    """Read the batch then item, checking identity before adopting any linked batch."""
    observations = copy.deepcopy(ctx.data.get("payout_observations") or {})
    history = observations.setdefault(name, [])
    expected = restore(ctx, ctx.data["writes"][name]["body"])["items"][0]
    last = {}
    for poll in range(MAX_POLLS):
        if poll:
            await ctx.sleep(POLL_SECONDS)
        response = await ctx.client.request(
            "GET",
            f"/v1/payments/payouts/{resource_id(batch)}",
            step=f"{name}_batch",
            params={"page_size": 1000, "total_required": "true"},
        )
        body = payload(response)
        items = body.get("items") or []
        sample = {
            "at": now(ctx),
            "batch_http_status": response.status_code,
            "batch_status": (body.get("batch_header") or {}).get("batch_status"),
            "item_count": len(items),
            "total_items": body.get("total_items"),
        }
        if response.status_code == 200 and items:
            total = body.get("total_items")
            if (
                len(items) != 1
                or (total is not None and str(total) != "1")
                or not matching_item(ctx, items[0], expected)
            ):
                raise PayoutCheckError("Batch item identity or count mismatch; adoption refused.")
            response = await ctx.client.request(
                "GET",
                f"/v1/payments/payouts-item/{resource_id(items[0]['payout_item_id'])}",
                step=f"{name}_item",
            )
            if response.status_code == 200:
                last = payload(response)
                if not matching_item(ctx, last, expected):
                    raise PayoutCheckError("Payout item identity mismatch; adoption refused.")
                timeline.record(
                    ctx,
                    "PAYOUT_SUBMITTED",
                    resource_id=batch,
                    amount_minor=cents(expected["amount"].get("value")),
                    currency=expected["amount"].get("currency"),
                    paypal_time=(body.get("batch_header") or {}).get("time_created"),
                )
                sample.update(
                    item_id=last.get("payout_item_id"), status=last.get("transaction_status")
                )
                item_event(ctx, last)
        history.append(sample)
        observations = copy.deepcopy(ctx.data.get("payout_observations") or {})
        observations[name] = history
        ctx.checkpoint(payout_observations=observations)
        if last.get("transaction_status") in target:
            break
    return {"item": last, "history": history, "item_count": history[-1]["item_count"]}


async def observe_balance(ctx, name, baseline, delta):
    """Save observations, never infer 'no change' from failed/missing balance reads."""
    histories = copy.deepcopy(ctx.data.get("balance_observations") or {})
    samples = histories.setdefault(name, [])
    initial = cents(baseline.get("available")) if baseline.get("status") == 200 else None

    def reflected(sample):
        value = cents(sample.get("available")) if sample.get("status") == 200 else None
        if initial is None or value is None or delta is None:
            return False
        return value >= initial + delta if delta >= 0 else value <= initial + delta

    limit = BALANCE_POLLS if initial is not None and delta is not None else 1
    while len(samples) < limit and not (samples and reflected(samples[-1])):
        if samples:
            await ctx.sleep(BALANCE_SECONDS)
        samples.append(await balance_snapshot(ctx, step=f"{name}_balance"))
        histories = copy.deepcopy(ctx.data.get("balance_observations") or {})
        histories[name] = samples
        ctx.checkpoint(balance_observations=histories)
    return {
        "observations": samples,
        "first_reflected_at": next((s["fetched_at"] for s in samples if reflected(s)), None),
        "baseline": baseline,
        "expected_delta_minor": delta,
        "notes": "Observed threshold only; D12 assesses freshness, fees and overlapping changes.",
    }


def webhooks(ctx, resource_ids):
    """C04 public deliveries, with elapsed time from the event's own PayPal timestamp."""
    wanted = {value for value in resource_ids if value}
    if not wanted:
        return []
    events = []
    for path in sorted((ctx.root / "webhooks").glob("*.json")):
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        resource = (entry.get("body") or {}).get("resource") or {}
        header = resource.get("batch_header") or {}
        if not wanted.intersection(
            {
                resource.get("id"),
                resource.get("payout_item_id"),
                resource.get("payout_batch_id"),
                header.get("payout_batch_id"),
            }
        ):
            continue
        try:
            delay = (
                datetime.fromisoformat(entry["received_at"])
                - datetime.fromisoformat(entry["create_time"])
            ).total_seconds()
        except (KeyError, TypeError, ValueError):
            delay = None
        events.append(
            {
                "event_type": entry.get("event_type"),
                "event_id": entry.get("event_id"),
                "received_at": entry.get("received_at"),
                "delay_seconds": delay,
                "file": path.relative_to(ctx.root).as_posix(),
            }
        )
    return sorted(events, key=lambda e: e.get("received_at") or "")


def findings(ctx, check_id, verdict, **details):
    return write_findings(ctx, {"check_id": check_id, "verdict": verdict, **details})
