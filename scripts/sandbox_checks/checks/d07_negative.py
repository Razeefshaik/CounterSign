"""D7: trigger PayPal's documented Payouts negative-test values and record what comes back.

The recordings become fixtures for the mock's fault harness (mock_paypal/faults.py,
NEGATIVE_VALUES). Source: https://developer.paypal.com/api/payouts/v5/standard/test-go-live/
"To trigger a simulation for the Payouts API, use a JSON pointer in the request payload or
use a path parameter in the request URI." Test values are case sensitive. The docs give
names only, never HTTP status codes, and say nothing about switching negative testing on
for the account or about a PayPal-Mock-Response header, so neither is assumed: if the
values are ignored, the recordings show it and the verdict is BLOCKED or DIFFERENT.

Safety: a create that is NOT simulated is a real $1.00 sandbox payout to maker-b. It is
recorded as SUCCEEDED (DIFFERENT), added to the timeline, never retried, and the remaining
create values are skipped. A create that loses its response is UNKNOWN and never resent.
"""

import os
import uuid
from datetime import datetime

from .. import timeline
from ..client import SandboxRequestError
from ..runner import CheckContext, Step
from .d08_dispute_create import now, payload, write_findings

DOCS = "https://developer.paypal.com/api/payouts/v5/standard/test-go-live/"
AMOUNT_MINOR = 100  # every create is $1.00 USD to maker-b
CURRENCY = "USD"
CREATE_TIMEOUT = 60.0  # ERRPYO036 simulates a timeout; leave room for a slow simulated reply

# Where each operation carries the test value (from the doc page above).
#   create:    POST /v1/payments/payouts, JSON pointer items[0]/note (docs example: ERRPYO002).
#   get_batch: GET /v1/payments/payouts/{payout_batch_id}, value as the path parameter
#              (docs example: GET /v1/payments/payouts/ERRPYO015).
#   get_item:  GET /v1/payments/payouts-item/{payout_item_id}, value as the path parameter.
#   cancel:    POST /v1/payments/payouts-item/{payout_item_id}/cancel, value in the path.
TRIGGERS = {
    "create": "body:items[0].note",
    "get_batch": "path:payout_batch_id",
    "get_item": "path:payout_item_id",
    "cancel": "path:payout_item_id",
}

# (operation, value, documented name). For batch values the name is batch_header.batch_status
# ("Simulated batch status"); for item values it is errors.name on a FAILED item.
# ERRPYO015 is ambiguous: PLAN D7 and the mock treat it as an item value, but the docs list it
# under "Show payout details" (the batch GET) and their example uses the batch path; the item
# table uses ERRPYO041 for CLOSED_MARKET. Both paths are probed (reads are safe to repeat);
# whichever answers CLOSED_MARKET settles where the mock should inject it.
PROBES = {
    "get_batch": (
        ("ERRPYOB002", "PENDING"),
        ("ERRPYOB003", "PROCESSING"),
        ("ERRPYOB004", "DENIED"),
        ("ERRPYO015", "CLOSED_MARKET"),
    ),
    "get_item": (
        ("ERRPYO015", "CLOSED_MARKET"),
        ("ERRPYO047", "RECEIVER_UNREGISTERED"),
        ("ERRPYO052", "RISK_DECLINE"),
    ),
    "cancel": (
        ("ERRPYO007", "ITEM_ALREADY_CANCELLED"),
        ("ERRPYO008", "ITEM_CANCELLATION_FAILED"),
        ("ERRPYO009", "ITEM_INCORRECT_STATUS"),
    ),
    "create": (
        ("ERRPYO005", "INSUFFICIENT_FUNDS"),
        ("ERRPYO006", "INTERNAL_ERROR"),
        ("ERRPYO014", "USER_BUSINESS_ERROR"),
        ("ERRPYO036", "REQUEST_TIMEOUT_EXCEEDED"),
        ("ERRPYO039", "PREVIOUS_REQUEST_IN_PROGRESS"),
    ),
}
# HTTP status the mock assumes today (faults.NEGATIVE_VALUES), kept for comparison only.
MOCK_STATUS = {
    "ERRPYO005": 422,
    "ERRPYO006": 500,
    "ERRPYO014": 422,
    "ERRPYO036": 504,
    "ERRPYO039": 409,
    "ERRPYO007": 400,
    "ERRPYO008": 400,
    "ERRPYO009": 400,
    "ERRPYO015": 200,
    "ERRPYO047": 200,
    "ERRPYO052": 200,
    "ERRPYOB002": 200,
    "ERRPYOB003": 200,
    "ERRPYOB004": 200,
}
BODY_TEXT_LIMIT = 2000


def uuid7(moment: datetime) -> uuid.UUID:
    """RFC 9562 UUIDv7: 48-bit Unix milliseconds, version 7, variant 10, 74 random bits."""
    millis = int(moment.timestamp() * 1000) & ((1 << 48) - 1)
    rand = int.from_bytes(os.urandom(10))
    rand_a = (rand >> 64) & 0xFFF
    rand_b = rand & ((1 << 62) - 1)
    return uuid.UUID(int=(millis << 80) | (0x7 << 76) | (rand_a << 64) | (0b10 << 62) | rand_b)


def new_key(ctx: CheckContext) -> str:
    return f"cs-{uuid7(ctx.clock())}-a1"


def route(operation: str, value: str) -> tuple[str, str]:
    return {
        "create": ("POST", "/v1/payments/payouts"),
        "get_batch": ("GET", f"/v1/payments/payouts/{value}"),
        "get_item": ("GET", f"/v1/payments/payouts-item/{value}"),
        "cancel": ("POST", f"/v1/payments/payouts-item/{value}/cancel"),
    }[operation]


def create_body(key: str, value: str, receiver: str) -> dict:
    return {
        "sender_batch_header": {
            "sender_batch_id": key,
            "email_subject": "CounterSign sandbox negative test",
        },
        "items": [
            {
                "recipient_type": "EMAIL",
                "receiver": receiver,
                "amount": {
                    "currency": CURRENCY,
                    "value": f"{AMOUNT_MINOR // 100}.{AMOUNT_MINOR % 100:02d}",
                },
                "note": value,
                "sender_item_id": f"{key}-i1",
            }
        ],
    }


def names_seen(data: dict) -> list[str]:
    """Every name-like field, most specific first: error name, item errors, then statuses."""
    found: list[str] = []
    items = [item for item in data.get("items") or [] if isinstance(item, dict)]
    header = data.get("batch_header") if isinstance(data.get("batch_header"), dict) else {}
    candidates = [data.get("name")]
    for holder in (data, *items):
        errors = holder.get("errors")
        if isinstance(errors, dict):
            candidates.append(errors.get("name"))
    candidates += [
        header.get("batch_status"),
        data.get("transaction_status"),
        *(item.get("transaction_status") for item in items),
    ]
    for name in candidates:
        if isinstance(name, str) and name and name not in found:
            found.append(name)
    return found


def response_body(response):
    try:
        return response.json()
    except ValueError:
        return response.text[:BODY_TEXT_LIMIT]


def probe_key(operation: str, value: str) -> str:
    return f"{operation}:{value}"


def save(ctx: CheckContext, row: dict) -> dict:
    probes = dict(ctx.data.get("probes") or {})
    probes[probe_key(row["operation"], row["value"])] = row
    ctx.checkpoint(probes=probes)
    return row


def base_row(operation: str, value: str, expected: str) -> dict:
    method, path = route(operation, value)
    return {
        "value": value,
        "operation": operation,
        "expected_name": expected,
        "method": method,
        "path": path,
        "trigger": TRIGGERS[operation],
        "docs": DOCS,
        "mock_status": MOCK_STATUS[value],
        "status": None,
        "error_name": None,
        "debug_id": None,
        "matched": None,
    }


def report(ctx: CheckContext, row: dict):
    if row["outcome"] == "RESPONDED":
        verdict = "matches" if row["matched"] else f"expected {row['expected_name']}"
        ctx.note(
            f"D7 {row['operation']} {row['value']}: HTTP {row['status']} "
            f"{row['error_name']} ({verdict})"
        )
    else:
        ctx.note(f"D7 {row['operation']} {row['value']}: {row['outcome']}")


async def probe(ctx: CheckContext, operation: str, value: str, expected: str) -> dict:
    """Send one test value once; POSTs are checkpointed first and never resent on resume."""
    saved = (ctx.data.get("probes") or {}).get(probe_key(operation, value))
    if saved and saved.get("outcome"):
        return saved
    method, path = route(operation, value)
    if saved and method == "POST":
        # Interrupted between the checkpoint and the outcome: the request may have reached
        # PayPal, so it stays UNKNOWN and is not sent again.
        row = {**saved, "outcome": "UNKNOWN", "unknown_reason": "interrupted; not resent"}
        report(ctx, row)
        return save(ctx, row)
    row = base_row(operation, value, expected)
    body = None
    if operation == "create":
        key = new_key(ctx)
        body = create_body(key, value, ctx.client.settings.maker_b_email)
        row.update(
            request_id=key,
            sender_batch_id=key,
            receiver_role="maker-b",
            amount_minor=AMOUNT_MINOR,
            currency=CURRENCY,
            request_body=body,
        )
    await ctx.client.authenticate()  # an auth failure must not leave a POST looking "sent"
    row["sent_at"] = now(ctx)
    save(ctx, row)
    try:
        response = await ctx.client.request(
            method,
            path,
            step=f"{operation}_{value.lower()}",
            request_id=row.get("request_id"),
            json=body,
            timeout=CREATE_TIMEOUT if operation == "create" else None,
        )
    except SandboxRequestError as error:
        row.update(outcome="UNKNOWN", transport_error=type(error).__name__)
        report(ctx, row)
        if operation == "create":
            ctx.note(f"D7: the {value} create may have paid $1.00 to maker-b; it is not resent.")
        return save(ctx, row)
    data = payload(response)
    names = names_seen(data)
    row.update(
        outcome="RESPONDED",
        status=response.status_code,
        error_name=names[0] if names else None,
        names_seen=names,
        debug_id=data.get("debug_id") or response.headers.get("paypal-debug-id"),
        matched=expected in names,
        response_body=response_body(response),
    )
    if operation == "create" and response.is_success:
        header = data.get("batch_header") if isinstance(data.get("batch_header"), dict) else {}
        batch_id = header.get("payout_batch_id")
        row.update(outcome="SUCCEEDED", matched=False, payout_batch_id=batch_id)
        save(ctx, row)
        ctx.note(
            f"D7 WARNING: create with {value} was NOT simulated (HTTP {response.status_code}). "
            f"A real $1.00 sandbox payout went to maker-b, batch {batch_id}. Not retrying; "
            "the remaining create values are skipped."
        )
        timeline.record(
            ctx,
            "PAYOUT_SUBMITTED",
            resource_id=batch_id or row["sender_batch_id"],
            amount_minor=AMOUNT_MINOR,
            currency=CURRENCY,
        )
        return row
    report(ctx, row)
    return save(ctx, row)


async def load_inputs(ctx: CheckContext) -> dict:
    return {
        "maker_b_configured": bool(ctx.client.settings.maker_b_email),
        "planned": [probe_key(op, value) for op, rows in PROBES.items() for value, _ in rows],
    }


async def run_reads_and_cancels(ctx: CheckContext, operation: str):
    for value, expected in PROBES[operation]:
        await probe(ctx, operation, value, expected)


async def batch_values(ctx: CheckContext):
    await run_reads_and_cancels(ctx, "get_batch")


async def item_values(ctx: CheckContext):
    await run_reads_and_cancels(ctx, "get_item")


async def cancel_values(ctx: CheckContext):
    await run_reads_and_cancels(ctx, "cancel")


async def create_values(ctx: CheckContext):
    """Run last, after the free reads, so a create only moves money if PayPal ignores it."""
    for value, expected in PROBES["create"]:
        probes = ctx.data.get("probes") or {}
        key = probe_key("create", value)
        if key in probes and probes[key].get("outcome"):
            continue
        moved = any(row.get("outcome") == "SUCCEEDED" for row in probes.values())
        reason = None
        if not ctx.data["maker_b_configured"]:
            reason = "SBX_MAKER_B_EMAIL is not set"
        elif moved and key not in probes:
            reason = "an earlier create was not simulated and moved real sandbox money"
        if reason:
            row = {**base_row("create", value, expected), "outcome": "SKIPPED", "reason": reason}
            report(ctx, row)
            save(ctx, row)
            continue
        await probe(ctx, "create", value, expected)


def value_results(rows: list[dict]) -> list[dict]:
    """One result per test value; a value matches if any of its probes did."""
    results = []
    for value in dict.fromkeys(row["value"] for row in rows):
        mine = [row for row in rows if row["value"] == value]
        matched_on = [row["operation"] for row in mine if row["matched"]]
        responded = [row for row in mine if row["outcome"] in {"RESPONDED", "SUCCEEDED"}]
        if matched_on:
            matched = True
        elif responded:
            matched = False
        else:
            matched = None
        results.append(
            {
                "value": value,
                "operations": [row["operation"] for row in mine],
                "expected_name": mine[0]["expected_name"],
                "matched": matched,
                "matched_on": matched_on,
                "outcomes": {row["operation"]: row["outcome"] for row in mine},
            }
        )
    return results


def decide(rows: list[dict], values: list[dict]) -> tuple[str, list[str]]:
    notes = []
    responded = [row for row in rows if row["outcome"] in {"RESPONDED", "SUCCEEDED"}]
    succeeded = [row for row in rows if row["outcome"] == "SUCCEEDED"]
    if succeeded:
        notes.append(
            "A create was not simulated and moved real sandbox money: "
            + ", ".join(
                f"{row['value']} -> batch {row.get('payout_batch_id')}" for row in succeeded
            )
        )
        return "DIFFERENT", notes
    if not responded:
        notes.append("No test value got a response; nothing could be tested.")
        return "BLOCKED", notes
    outcomes = {(row["status"], row["error_name"]) for row in responded}
    if not any(row["matched"] for row in rows) and len(responded) > 1 and len(outcomes) == 1:
        status, name = next(iter(outcomes))
        notes.append(
            f"Every value returned the same response (HTTP {status} {name}); negative testing "
            "appears not to be active for this account, or the triggers differ from the docs."
        )
        return "BLOCKED", notes
    if any(value["matched"] is False for value in values):
        return "DIFFERENT", notes
    if all(value["matched"] for value in values):
        return "CONFIRMED", notes
    notes.append("No value contradicted the docs, but some got no response (see unknown_values).")
    return "BLOCKED", notes


async def findings(ctx: CheckContext) -> dict:
    """Write findings.json; state keeps probes keyed by operation:value, not the list."""
    probes = ctx.data.get("probes") or {}
    rows = [probes[key] for key in ctx.data["planned"] if key in probes]
    for row in rows:
        if row["outcome"] == "SUCCEEDED":  # backfill if a crash beat the timeline write
            timeline.record(
                ctx,
                "PAYOUT_SUBMITTED",
                resource_id=row.get("payout_batch_id") or row["sender_batch_id"],
                amount_minor=AMOUNT_MINOR,
                currency=CURRENCY,
                at=row["sent_at"],
            )
    values = value_results(rows)
    verdict, notes = decide(rows, values)
    fixtures = {}
    for row in rows:
        if row["outcome"] == "RESPONDED" and (row["matched"] or row["value"] not in fixtures):
            fixtures[row["value"]] = {
                "operation": row["operation"],
                "status": row["status"],
                "name": row["error_name"],
                "mock_status": row["mock_status"],
            }
    notes.append(
        "ERRPYO015 is probed on both the batch and the item path; matched_on shows which one "
        "PayPal honours."
    )
    unknown_creates = [
        {"value": row["value"], "sender_batch_id": row.get("sender_batch_id")}
        for row in rows
        if row["operation"] == "create" and row["outcome"] == "UNKNOWN"
    ]
    if unknown_creates:
        notes.append(
            "Some creates lost their response and were not resent: check maker-b's sandbox "
            "activity for these sender_batch_ids, since each may have paid $1.00."
        )
    finding = write_findings(
        ctx,
        {
            "check_id": "D7",
            "verdict": verdict,
            "docs": DOCS,
            "values": values,
            "probes": rows,
            "mismatched_values": [v["value"] for v in values if v["matched"] is False],
            "unknown_values": [v["value"] for v in values if v["matched"] is None],
            "money_moved": [
                {
                    "value": row["value"],
                    "payout_batch_id": row.get("payout_batch_id"),
                    "sender_batch_id": row.get("sender_batch_id"),
                    "amount_minor": AMOUNT_MINOR,
                    "currency": CURRENCY,
                }
                for row in rows
                if row["outcome"] == "SUCCEEDED"
            ],
            "unknown_creates": unknown_creates,
            "fixtures": fixtures,
            "notes": notes,
        },
    )
    return {"verdict": finding["verdict"], "value_results": finding["values"]}


def get_steps() -> list[Step]:
    return [
        Step("load_inputs", load_inputs),
        Step("batch_values", batch_values),
        Step("item_values", item_values),
        Step("cancel_values", cancel_values),
        Step("create_values", create_values),
        Step("findings", findings),
    ]
