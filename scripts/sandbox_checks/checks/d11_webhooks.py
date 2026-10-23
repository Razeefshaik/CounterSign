"""D11: webhook timing, duplicates, order, gaps and signature verification (verify #6, F11).

Reads the C04 catcher's stored deliveries (webhooks/<name>.json, raw/webhooks/<name>.body and
.headers.json) and the shared timeline from D2 to D10. Sends one simulator event and resends
one real event, each checkpointed before sending and never re-sent on resume. Signature
verification is read-only and checkpointed per delivery, so a resumed run skips settled ones.

Resource IDs come from the documented resource schemas, chosen by event-name family: payout
item resource.payout_item_id, payout batch resource.batch_header.payout_batch_id, capture or
refund resource.id (a refund also names its capture in its "up" link), dispute
resource.dispute_id. The event-names page shows no sample payloads, so this is [pending D11].
"""

import json
import statistics
from pathlib import Path

from .. import timeline
from ..client import SandboxRequestError
from ..runner import CheckContext, Step
from .d08_dispute_create import RESOURCE_ID, now, parse_time, payload, write_findings

VERIFY_PATH = "/v1/notifications/verify-webhook-signature"
SIMULATE_PATH = "/v1/notifications/simulate-event"
EVENTS_PATH = "/v1/notifications/webhooks-events"
SIMULATED_EVENT_TYPE = "PAYMENT.CAPTURE.COMPLETED"
ARRIVAL_POLL_SECONDS = 10
ARRIVAL_MAX_POLLS = 30  # five minutes for a simulated or resent delivery to arrive
LIST_PAGE_SIZE = 50
SIGNATURE_HEADERS = {
    "auth_algo": "paypal-auth-algo",
    "cert_url": "paypal-cert-url",
    "transmission_id": "paypal-transmission-id",
    "transmission_sig": "paypal-transmission-sig",
    "transmission_time": "paypal-transmission-time",
}
VERIFIED = ("SUCCESS", "FAILURE")
CLAIM = "Webhook authenticity via the verify API; deliveries can be late, duplicated or missing."
NOTES = [
    (
        "verify-webhook-signature takes webhook_event as JSON, so PayPal checks a re-serialised "
        "event, not the exact received bytes; the raw bytes are kept (raw/webhooks/*.body) and "
        "the exact-bytes concern is recorded, not solved"
    ),
    (
        "simulator events use the mock webhook ID WEBHOOK_ID, so FAILURE is expected for them "
        "(F11); they are excluded from the verdict"
    ),
    (
        "resource IDs are read by event-name family from the documented resource schemas; the "
        "event-names page shows no sample payloads [pending D11]"
    ),
    "a resent event shows up as a duplicate marked induced_by_resend",
    f"the event list was read with page_size={LIST_PAGE_SIZE}; later pages were not followed",
]


def as_dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def seconds_between(start, end) -> float | None:
    try:
        return round((parse_time(end) - parse_time(start)).total_seconds(), 3)
    except (TypeError, ValueError):
        return None


def capture_from_links(resource: dict) -> list[str]:
    """A refund's "up" link points at its capture: .../v2/payments/captures/<id>."""
    found = []
    for link in resource.get("links") or []:
        href = as_dict(link).get("href")
        if as_dict(link).get("rel") == "up" and isinstance(href, str) and "/captures/" in href:
            found.append(href.rstrip("/").rsplit("/", 1)[-1])
    return found


def resource_ids(event_type, body) -> list[str]:
    resource = as_dict(as_dict(body).get("resource"))
    event_type = event_type if isinstance(event_type, str) else ""
    if event_type.startswith("PAYMENT.PAYOUTS-ITEM."):
        ids = [resource.get("payout_item_id")]
    elif event_type.startswith("PAYMENT.PAYOUTSBATCH."):
        ids = [as_dict(resource.get("batch_header")).get("payout_batch_id")]
    elif event_type.startswith("CUSTOMER.DISPUTE."):
        ids = [resource.get("dispute_id")]
    else:
        ids = [resource.get("id"), *capture_from_links(resource)]
    return list(dict.fromkeys(value for value in ids if isinstance(value, str) and value))


def load_deliveries(root: Path) -> list[dict]:
    """Every stored delivery in received order (names start with the receive timestamp)."""
    deliveries = []
    for path in sorted(Path(root, "webhooks").glob("*.json")):
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(stored, dict):
            continue
        deliveries.append(
            {
                "name": path.stem,
                "event_id": stored.get("event_id"),
                "event_type": stored.get("event_type"),
                "resource_type": stored.get("resource_type"),
                "create_time": stored.get("create_time"),
                "received_at": stored.get("received_at"),
                "resource_ids": resource_ids(stored.get("event_type"), stored.get("body")),
                "delay_seconds": seconds_between(
                    stored.get("create_time"), stored.get("received_at")
                ),
            }
        )
    return deliveries


def is_simulated(ctx: CheckContext, delivery: dict) -> bool:
    """Simulator copies are matched by name, or by event ID when one arrived late."""
    simulator = as_dict(ctx.data.get("simulator"))
    return delivery["name"] in (simulator.get("delivery_names") or []) or (
        bool(simulator.get("event_id")) and delivery["event_id"] == simulator["event_id"]
    )


def real_deliveries(ctx: CheckContext) -> list[dict]:
    return [d for d in load_deliveries(ctx.root) if not is_simulated(ctx, d)]


def summary(values: list[float]) -> dict:
    if not values:
        return {"count": 0, "min": None, "median": None, "max": None}
    return {
        "count": len(values),
        "min": min(values),
        "median": round(statistics.median(values), 3),
        "max": max(values),
    }


def delays(deliveries: list[dict]) -> dict:
    by_type: dict[str, list[float]] = {}
    for delivery in deliveries:
        if delivery["delay_seconds"] is not None:
            by_type.setdefault(str(delivery["event_type"]), []).append(delivery["delay_seconds"])
    return {
        "overall": summary([value for values in by_type.values() for value in values]),
        "by_event_type": {name: summary(values) for name, values in sorted(by_type.items())},
        "without_times": sum(d["delay_seconds"] is None for d in deliveries),
    }


def duplicates(deliveries: list[dict], resent_event_id: str | None) -> dict:
    names: dict[str, list[dict]] = {}
    for delivery in deliveries:
        if isinstance(delivery["event_id"], str) and delivery["event_id"]:
            names.setdefault(delivery["event_id"], []).append(delivery)
    return {
        event_id: {
            "count": len(copies),
            "event_type": copies[0]["event_type"],
            "received_at": [copy["received_at"] for copy in copies],
            "names": [copy["name"] for copy in copies],
            "induced_by_resend": event_id == resent_event_id,
        }
        for event_id, copies in names.items()
        if len(copies) > 1
    }


def out_of_order(deliveries: list[dict]) -> list[dict]:
    """Per resource: a delivery created before one that was already received."""
    found = []
    latest: dict[str, dict] = {}  # resource id -> received delivery with the newest create_time
    for delivery in deliveries:
        try:
            created = parse_time(delivery["create_time"])
        except (TypeError, ValueError):
            continue
        if created.tzinfo is None:
            continue  # PayPal times carry a zone; a naive one cannot be ordered against them
        for resource in delivery["resource_ids"]:
            newest = latest.get(resource)
            if (
                newest
                and newest["delivery"]["event_id"] != delivery["event_id"]
                and created < newest["created"]
            ):
                found.append(
                    {
                        "resource_id": resource,
                        "event_id": delivery["event_id"],
                        "event_type": delivery["event_type"],
                        "create_time": delivery["create_time"],
                        "received_after": newest["delivery"]["event_id"],
                        "received_after_type": newest["delivery"]["event_type"],
                    }
                )
            if not newest or created > newest["created"]:
                latest[resource] = {"delivery": delivery, "created": created}
    return found


def missing(deliveries: list[dict], events: list[dict]) -> list[dict]:
    """Each timeline event with the expected webhook types that arrived for its resource."""
    report = []
    for entry in events:
        expected = timeline.EVENTS.get(entry.get("event"), ((), None))[0]
        matches = [
            d
            for d in deliveries
            if d["event_type"] in expected and entry.get("resource_id") in d["resource_ids"]
        ]
        first = matches[0]["received_at"] if matches else None
        report.append(
            {
                "event": entry.get("event"),
                "source": entry.get("source"),
                "resource_id": entry.get("resource_id"),
                "observed_at": entry.get("at"),
                "expected": list(expected),
                "arrived": sorted({d["event_type"] for d in matches}),
                "missing": not matches,
                "first_received_at": first,
                "webhook_minus_observed_seconds": seconds_between(entry.get("at"), first),
            }
        )
    return report


def analyze(deliveries: list[dict], events: list[dict], resent_event_id=None) -> dict:
    gaps = missing(deliveries, events)
    return {
        "deliveries": len(deliveries),
        "delay_seconds": delays(deliveries),
        "duplicates": duplicates(deliveries, resent_event_id),
        "out_of_order": out_of_order(deliveries),
        "timeline_events": len(events),
        "missing": [gap for gap in gaps if gap["missing"]],
        "matched": [gap for gap in gaps if not gap["missing"]],
        "without_resource_id": [d["name"] for d in deliveries if not d["resource_ids"]],
        "per_delivery": deliveries,
    }


async def verify(ctx: CheckContext, name: str) -> dict:
    """One verify call; anything other than SUCCESS or FAILURE stays unknown (None)."""
    result = {"verified_at": now(ctx), "status": None, "verification_status": None}
    raw_dir = ctx.root / "raw" / "webhooks"
    try:
        raw_body = (raw_dir / f"{name}.body").read_bytes()
        headers = json.loads((raw_dir / f"{name}.headers.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {**result, "error": "RAW_FILES_UNREADABLE"}
    try:
        event = json.loads(raw_body)
    except (ValueError, UnicodeDecodeError):
        return {**result, "error": "BODY_NOT_JSON"}
    fields = {field: as_dict(headers).get(header) for field, header in SIGNATURE_HEADERS.items()}
    absent = sorted(
        field for field, value in fields.items() if not isinstance(value, str) or not value
    )
    if absent:
        return {**result, "error": "MISSING_HEADERS", "missing_headers": absent}
    try:
        response = await ctx.client.request(
            "POST",
            VERIFY_PATH,
            step="verify_signature",
            json={**fields, "webhook_id": ctx.client.settings.webhook_id, "webhook_event": event},
        )
    except SandboxRequestError as error:
        return {**result, "error": type(error).__name__}
    data = payload(response)
    status = data.get("verification_status")
    return {
        **result,
        "status": response.status_code,
        "verification_status": (
            status if response.status_code == 200 and status in VERIFIED else None
        ),
        "error_name": data.get("name"),
    }


async def wait_for_delivery(ctx: CheckContext, matches) -> list[dict]:
    """Poll the catcher's folder, bounded, until a matching delivery is stored."""
    for poll in range(ARRIVAL_MAX_POLLS):
        if poll:
            await ctx.sleep(ARRIVAL_POLL_SECONDS)
        found = [d for d in load_deliveries(ctx.root) if matches(d)]
        if found:
            return found
    return []


async def inputs(ctx: CheckContext) -> dict:
    webhook_id_set = bool(ctx.client.settings.webhook_id.strip())
    deliveries = load_deliveries(ctx.root)
    ctx.note(f"D11: {len(deliveries)} stored deliveries; webhook ID set: {webhook_id_set}")
    return {
        "webhook_id_set": webhook_id_set,
        "deliveries_at_start": [d["name"] for d in deliveries],
    }


async def list_events(ctx: CheckContext) -> dict:
    """Read-only backfill probe; repeats freely on resume."""
    entry = {"checked_at": now(ctx), "status": None, "works": False}
    try:
        response = await ctx.client.request(
            "GET", EVENTS_PATH, step="list_events", params={"page_size": LIST_PAGE_SIZE}
        )
    except SandboxRequestError as error:
        return {"event_list": {**entry, "error": type(error).__name__}}
    data = payload(response)
    events = data.get("events") if isinstance(data.get("events"), list) else None
    stored = {d["event_id"] for d in load_deliveries(ctx.root)}
    listed = [as_dict(event) for event in events or []]
    return {
        "event_list": {
            **entry,
            "status": response.status_code,
            "works": response.status_code == 200 and events is not None,
            "error_name": data.get("name"),
            "count": len(listed),
            "has_next_link": any(
                as_dict(link).get("rel") == "next" for link in data.get("links") or []
            ),
            "listed_not_stored": [
                {"event_id": event.get("id"), "event_type": event.get("event_type")}
                for event in listed
                if event.get("id") not in stored
            ],
        }
    }


async def simulate_event(ctx: CheckContext) -> dict | None:
    if "simulator" in ctx.data:
        return None  # sent, or interrupted while sending: never re-sent
    if not ctx.data["webhook_id_set"]:
        return {"simulator": {"skipped": "NO_WEBHOOK_ID"}}
    await ctx.manual.confirm(
        "D11 will send one simulator event. Check that the webhook catcher "
        "(uvicorn scripts.sandbox_checks.catcher:app --port 8000) and its HTTPS tunnel are "
        "running, then press Enter."
    )
    simulator = {"event_type": SIMULATED_EVENT_TYPE, "sent_at": now(ctx), "status": None}
    ctx.checkpoint(simulator=simulator)
    response = await ctx.client.request(
        "POST",
        SIMULATE_PATH,
        step="simulate_event",
        json={"webhook_id": ctx.client.settings.webhook_id, "event_type": SIMULATED_EVENT_TYPE},
    )
    data = payload(response)
    event_id = data.get("id") if isinstance(data.get("id"), str) else None
    return {
        "simulator": {
            **simulator,
            "status": response.status_code,
            "event_id": event_id,
            "error_name": data.get("name"),
        }
    }


async def await_simulated(ctx: CheckContext) -> dict | None:
    simulator = dict(ctx.data["simulator"])
    if simulator.get("skipped") or "delivery_names" in simulator:
        return None
    if simulator["status"] is not None and not 200 <= simulator["status"] < 300:
        return {"simulator": {**simulator, "delivery_names": [], "arrived": False}}
    before = set(ctx.data["deliveries_at_start"])
    event_id = simulator.get("event_id")

    def matches(delivery):
        if event_id:
            return delivery["event_id"] == event_id
        # The response was lost: fall back to the type and the send time.
        after_send = seconds_between(simulator["sent_at"], delivery["received_at"])
        return (
            delivery["name"] not in before
            and delivery["event_type"] == SIMULATED_EVENT_TYPE
            and after_send is not None
            and after_send >= 0
        )

    found = await wait_for_delivery(ctx, matches)
    simulator.update(
        matched_by="event_id" if event_id else "type_and_time",
        delivery_names=[d["name"] for d in found],
        arrived=bool(found),
        delay_seconds=found[0]["delay_seconds"] if found else None,
    )
    if found:
        result = await verify(ctx, found[0]["name"])
        status = result["verification_status"]
        # F11 expects FAILURE; an unknown result neither confirms nor contradicts it.
        simulator.update(verification=result, matches_f11=status == "FAILURE" if status else None)
    ctx.note(f"D11: simulator delivery arrived: {simulator['arrived']}")
    return {"simulator": simulator}


async def resend_event(ctx: CheckContext) -> dict | None:
    if "resend" in ctx.data:
        return None  # sent, or interrupted while sending: never re-sent
    if not ctx.data["webhook_id_set"]:
        return {"resend": {"skipped": "NO_WEBHOOK_ID"}}
    target = next(
        (
            d
            for d in real_deliveries(ctx)
            if isinstance(d["event_id"], str) and RESOURCE_ID.fullmatch(d["event_id"])
        ),
        None,
    )
    if target is None:
        return {"resend": {"skipped": "NO_REAL_DELIVERY"}}
    resend = {
        "event_id": target["event_id"],
        "event_type": target["event_type"],
        "sent_at": now(ctx),
        "status": None,
    }
    ctx.checkpoint(resend=resend)
    response = await ctx.client.request(
        "POST",
        f"{EVENTS_PATH}/{target['event_id']}/resend",
        step="resend_event",
        json={"webhook_ids": [ctx.client.settings.webhook_id]},
    )
    data = payload(response)
    return {
        "resend": {
            **resend,
            "status": response.status_code,
            "works": 200 <= response.status_code < 300,
            "error_name": data.get("name"),
        }
    }


async def await_resent(ctx: CheckContext) -> dict | None:
    resend = dict(ctx.data["resend"])
    if resend.get("skipped") or "duplicate_names" in resend:
        return None
    if resend.get("status") is not None and not resend.get("works"):
        return {"resend": {**resend, "duplicate_names": [], "duplicate_arrived": False}}
    before = set(ctx.data["deliveries_at_start"])
    found = await wait_for_delivery(
        ctx, lambda d: d["event_id"] == resend["event_id"] and d["name"] not in before
    )
    ctx.note(f"D11: resent delivery arrived: {bool(found)}")
    return {
        "resend": {
            **resend,
            "duplicate_names": [d["name"] for d in found],
            "duplicate_arrived": bool(found),
        }
    }


async def analysis(ctx: CheckContext) -> dict:
    report = analyze(
        real_deliveries(ctx),
        timeline.load_all(ctx.root),
        as_dict(ctx.data.get("resend")).get("event_id"),
    )
    ctx.note(
        f"D11: {report['deliveries']} deliveries, {len(report['duplicates'])} duplicated, "
        f"{len(report['out_of_order'])} out of order, {len(report['missing'])} missing"
    )
    return {"analysis": report}


async def verify_signatures(ctx: CheckContext) -> dict | None:
    if not ctx.data["webhook_id_set"]:
        return None
    results = dict(ctx.data.get("verifications") or {})
    for delivery in real_deliveries(ctx):
        name = delivery["name"]
        if as_dict(results.get(name)).get("verification_status") in VERIFIED:
            continue  # settled before an interruption
        results[name] = {"event_id": delivery["event_id"], **await verify(ctx, name)}
        ctx.checkpoint(verifications=results)
    return {"verifications": results}


def signature_summary(ctx: CheckContext) -> dict:
    real = {d["name"] for d in real_deliveries(ctx)}
    results = {
        name: result
        for name, result in as_dict(ctx.data.get("verifications")).items()
        if name in real
    }
    statuses = [result.get("verification_status") for result in results.values()]
    return {
        "real_deliveries": len(results),
        "success": statuses.count("SUCCESS"),
        "failure": statuses.count("FAILURE"),
        "unknown": sum(status not in VERIFIED for status in statuses),
        "failed": sorted(
            name for name, r in results.items() if r.get("verification_status") == "FAILURE"
        ),
        "unknown_names": sorted(
            name for name, r in results.items() if r.get("verification_status") not in VERIFIED
        ),
        "results": results,
    }


async def findings(ctx: CheckContext) -> dict:
    signatures = signature_summary(ctx)
    if not ctx.data["webhook_id_set"]:
        verdict, reason = "BLOCKED", "PAYPAL_WEBHOOK_ID is not set"
    elif not signatures["real_deliveries"]:
        verdict, reason = "BLOCKED", "no stored real deliveries to verify"
    elif signatures["failure"]:
        verdict, reason = "DIFFERENT", "a real delivery returned FAILURE"
    elif signatures["unknown"]:
        verdict, reason = "BLOCKED", "some real deliveries could not be verified (unknown)"
    else:
        verdict, reason = "CONFIRMED", "every real delivery verified SUCCESS"
    return write_findings(
        ctx,
        {
            "check_id": "D11",
            "verdict": verdict,
            "verdict_reason": reason,
            "claim": CLAIM,
            "signatures": signatures,
            "simulator": ctx.data["simulator"],
            "backfill": {"event_list": ctx.data["event_list"], "resend": ctx.data["resend"]},
            "analysis": ctx.data["analysis"],
            "notes": NOTES,
        },
    )


def get_steps() -> list[Step]:
    return [
        Step("inputs", inputs),
        Step("list_events", list_events),
        Step("simulate_event", simulate_event),
        Step("await_simulated", await_simulated),
        Step("resend_event", resend_event),
        Step("await_resent", await_resent),
        Step("analysis", analysis),
        Step("verify_signatures", verify_signatures),
        Step("findings", findings),
    ]
