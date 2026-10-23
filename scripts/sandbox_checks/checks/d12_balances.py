"""D12: balance freshness, consistency and reflection lag (verify #5, C07).

The handoff claims balances can lag up to 3 hours. Readings come from the runner's
balances.jsonl, D8's and D10's balance snapshots and one fresh reading taken here. Events
come from the shared D2 to D10 timeline. Transaction Search lag is measured once, for the
earliest capture. Failed or missing readings are BLOCKED evidence, never "not reflected".
"""

import json
import statistics
from datetime import datetime, timedelta
from pathlib import Path

from .. import timeline
from ..client import SandboxRequestError
from ..recorder import timestamp
from ..runner import CheckContext, Step
from .d08_dispute_create import (
    RESOURCE_ID,
    balance_snapshot,
    minor,
    now,
    payload,
    write_findings,
)

CLAIM_SECONDS = 3 * 3600
TXN_INTERVAL_SECONDS = 300
TXN_OBSERVE_SECONDS = 3 * 3600
TXN_LOOKBACK = timedelta(days=1)
TXN_MAX_RANGE = timedelta(days=30)  # the documented maximum is 31 days
EXAMPLES = 5
BALANCES = ("available", "withheld", "total")
TIME_FIELDS = ("last_refresh_time", "as_of_time")
DEFAULT_CURRENCY = "USD"
NOTES = (
    (
        "event 'at' is when a check observed the change, an upper bound on PayPal's own time, "
        "so lags measured from it can understate the true lag; paypal_time is shown where a "
        "check recorded it"
    ),
    (
        "a reflection lag is the reflecting reading's fetched_at (upper bound); the last valid "
        "unreflected reading gives the lower bound; resolution is the polling interval"
    ),
    (
        "an event counts as reflected once its balance moves the expected way by at least half "
        "of amount_minor (any amount when unknown), because fees make net movements smaller "
        "than the gross amount; change_minor shows the move"
    ),
    (
        "overlapping events in the same window can confound attribution; such rows list "
        "overlapping_events, and a not-reflected row with an overlapping opposite event is "
        "BLOCKED"
    ),
    (
        "not reflected requires a valid reading more than 3 hours after the event; failed or "
        "missing readings make a row BLOCKED, never not reflected"
    ),
    "events without a currency are assumed USD; D8 and D10 snapshots carry USD only",
    (
        "fetched_at is the local clock; clock skew shifts freshness lags, and a negative lag "
        "indicates skew"
    ),
    "PayPal times without a UTC offset are treated as missing",
    (
        "Transaction Search is queried with transaction_id = capture ID, assuming the capture "
        "ID is the searchable transaction ID (unverified)"
    ),
)


def parse_time(value) -> datetime | None:
    """An aware datetime; None when absent, malformed or without a UTC offset."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def seconds(delta: timedelta) -> float:
    return round(delta.total_seconds(), 3)


def search_time(value: datetime) -> str:
    """RFC 3339 with whole seconds, as Transaction Search documents."""
    return timestamp(value.replace(microsecond=0))


# Readings: one normalised shape for poller lines and d08-style snapshots.


def make_reading(source, fetched_at, status, *, amounts, times, error=None) -> dict:
    return {
        "source": source,
        "fetched_at": fetched_at,
        "status": status,
        "error": error,
        "last_refresh_time": times.get("last_refresh_time"),
        "as_of_time": times.get("as_of_time"),
        "amounts": amounts,
    }


def body_amounts(body) -> dict[str, dict]:
    """Per-currency minor amounts from a GET /v1/reporting/balances body."""
    entries = body.get("balances") if isinstance(body, dict) else None
    amounts: dict[str, dict] = {}
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        total = entry.get("total_balance") if isinstance(entry.get("total_balance"), dict) else {}
        currency = entry.get("currency") or total.get("currency_code")
        if isinstance(currency, str):
            amounts[currency] = {
                name: minor(value.get("value"))
                if isinstance(value := entry.get(f"{name}_balance"), dict)
                else None
                for name in BALANCES
            }
    return amounts


def jsonl_readings(path: Path) -> tuple[list[dict], int]:
    """Readings from the runner's balances.jsonl, plus a count of unusable lines."""
    if not path.exists():
        return [], 0
    readings, malformed = [], 0
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            malformed += 1
            continue
        if not isinstance(row, dict) or parse_time(row.get("fetched_at")) is None:
            malformed += 1
            continue
        body = row.get("response_body") if isinstance(row.get("response_body"), dict) else {}
        readings.append(
            make_reading(
                f"balances.jsonl:{number}",
                row["fetched_at"],
                row.get("response_status"),
                amounts=body_amounts(body),
                times=body,
                error=row.get("error"),
            )
        )
    return readings, malformed


def snapshot_reading(source: str, snapshot) -> dict | None:
    """A d08.balance_snapshot result (USD only) as a reading."""
    if not isinstance(snapshot, dict) or parse_time(snapshot.get("fetched_at")) is None:
        return None
    values = {name: minor(snapshot.get(name)) for name in BALANCES}
    has_value = any(value is not None for value in values.values())
    return make_reading(
        source,
        snapshot["fetched_at"],
        snapshot.get("status"),
        amounts={DEFAULT_CURRENCY: values} if has_value else {},
        times=snapshot,
    )


def collect_readings(ctx: CheckContext) -> tuple[list[tuple[datetime, dict]], int]:
    readings, malformed = jsonl_readings(Path(ctx.root) / "balances.jsonl")
    d8, d10 = ctx.load_check_data("D8"), ctx.load_check_data("D10")
    snapshots = [
        ("D12.fresh_balance", ctx.data.get("fresh_balance")),
        ("D8.baseline_balance", d8.get("baseline_balance")),
    ]
    snapshots += [
        (f"D8.hold_observations[{index}]", item.get("balance") if isinstance(item, dict) else None)
        for index, item in enumerate(d8.get("hold_observations") or [])
    ]
    snapshots += [
        (f"D10.release_observations[{index}]", item)
        for index, item in enumerate(d10.get("release_observations") or [])
    ]
    readings += [
        reading for source, snapshot in snapshots if (reading := snapshot_reading(source, snapshot))
    ]
    timed = [(parse_time(reading["fetched_at"]), reading) for reading in readings]
    return sorted(timed, key=lambda pair: pair[0]), malformed


def is_valid(reading: dict) -> bool:
    """HTTP 200 with at least one balance amount."""
    return reading["status"] == 200 and any(
        value is not None for amounts in reading["amounts"].values() for value in amounts.values()
    )


def value_of(reading: dict, currency: str, balance: str) -> int | None:
    if reading["status"] != 200:
        return None
    return (reading["amounts"].get(currency) or {}).get(balance)


def where(reading: dict) -> dict:
    return {"source": reading["source"], "fetched_at": reading["fetched_at"]}


# Part 1: freshness. Part 2: available + withheld == total.


def lag_stats(lags: list[float]) -> dict:
    if not lags:
        return {"count": 0, "min_seconds": None, "median_seconds": None, "max_seconds": None}
    return {
        "count": len(lags),
        "min_seconds": min(lags),
        "median_seconds": round(statistics.median(lags), 3),
        "max_seconds": max(lags),
    }


def freshness(valid: list[tuple[datetime, dict]]) -> dict:
    result: dict = {"stale_examples": []}
    for field in TIME_FIELDS:
        lags, missing = [], 0
        for fetched, reading in valid:
            stamp = parse_time(reading[field])
            if stamp is None:
                missing += 1
                continue
            lag = seconds(fetched - stamp)
            lags.append(lag)
            if lag > CLAIM_SECONDS and len(result["stale_examples"]) < EXAMPLES:
                result["stale_examples"].append(
                    {**where(reading), "field": field, "value": reading[field], "lag_seconds": lag}
                )
        result[field] = {
            **lag_stats(lags),
            "over_3h": sum(lag > CLAIM_SECONDS for lag in lags),
            "missing_or_unparseable": missing,
        }
    measured = any(result[field]["count"] for field in TIME_FIELDS)
    stale = any(result[field]["over_3h"] for field in TIME_FIELDS)
    result["any_over_3h"] = stale
    result["verdict"] = "BLOCKED" if not measured else "DIFFERENT" if stale else "CONFIRMED"
    return result


def consistency(valid: list[tuple[datetime, dict]]) -> dict:
    checked = incomplete = mismatched = 0
    examples = []
    for _, reading in valid:
        for currency, amounts in sorted(reading["amounts"].items()):
            if any(amounts[name] is None for name in BALANCES):
                incomplete += 1
                continue
            checked += 1
            difference = amounts["available"] + amounts["withheld"] - amounts["total"]
            if difference:
                mismatched += 1
                if len(examples) < EXAMPLES:
                    examples.append(
                        {
                            **where(reading),
                            "currency": currency,
                            **{f"{name}_minor": amounts[name] for name in BALANCES},
                            "difference_minor": difference,
                        }
                    )
    if not checked:
        verdict = "BLOCKED"
    else:
        verdict = "DIFFERENT" if mismatched else "CONFIRMED"
    return {
        "verdict": verdict,
        "checked": checked,
        "mismatches": mismatched,
        "incomplete": incomplete,
        "mismatch_examples": examples,
    }


# Part 3: when does each timeline event show up in a balance?


def effect(event: dict):
    return timeline.EVENTS.get(event.get("event"), (None, None))[1]


def label(event: dict) -> str:
    return f"{event.get('event')}:{event.get('resource_id')}"


def overlapping(event: dict, events: list[dict], start: datetime, end: datetime) -> list[dict]:
    """Other balance-affecting events in the same currency inside (start, end]."""
    currency = event.get("currency") or DEFAULT_CURRENCY
    found = []
    for other in events:
        at = parse_time(other.get("at"))
        if (
            other is not event
            and effect(other)
            and (other.get("currency") or DEFAULT_CURRENCY) == currency
            and at is not None
            and start < at <= end
        ):
            found.append(other)
    return found


def reflection_row(event: dict, readings: list[tuple[datetime, dict]], events: list[dict]) -> dict:
    balance, direction = effect(event)
    currency = event.get("currency") or DEFAULT_CURRENCY
    amount = event.get("amount_minor")
    # Fees make net movements smaller than the gross amount, so half of it counts.
    threshold = max(1, -(-amount // 2)) if isinstance(amount, int) and amount > 0 else 1
    row = {
        "event": event.get("event"),
        "source": event.get("source"),
        "resource_id": event.get("resource_id"),
        "at": event.get("at"),
        "paypal_time": event.get("paypal_time"),
        "balance": balance,
        "direction": direction,
        "amount_minor": amount,
        "threshold_minor": threshold,
        "currency": currency,
        "currency_assumed": not event.get("currency"),
        "overlapping_events": [],
    }
    at = parse_time(event.get("at"))
    if at is None:
        return {**row, "result": "BLOCKED", "reason": "event time is missing or malformed"}
    baseline = None
    gaps_before = 0
    for fetched, reading in readings:
        if fetched > at:
            break
        value = value_of(reading, currency, balance)
        if value is None:
            gaps_before += 1
        else:
            baseline, gaps_before = (fetched, reading, value), 0
    if baseline is None:
        return {
            **row,
            "result": "BLOCKED",
            "reason": f"no valid {balance} {currency} reading at or before the event",
        }
    base_time, base_reading, base_value = baseline
    row["baseline"] = {**where(base_reading), "value_minor": base_value}
    row["failed_readings_between_baseline_and_event"] = gaps_before
    failed_after = 0
    last_unreflected = reflected = None
    for fetched, reading in readings:
        if fetched <= at:
            continue
        value = value_of(reading, currency, balance)
        if value is None:
            failed_after += 1
        elif (value - base_value) * direction >= threshold:
            reflected = (fetched, reading, value)
            break
        else:
            last_unreflected = fetched
    row["failed_readings_after_event"] = failed_after
    if reflected:
        fetched, reading, value = reflected
        lower = seconds(last_unreflected - at) if last_unreflected else 0.0
        end = fetched
        row.update(
            result="REFLECTED",
            lag_seconds=seconds(fetched - at),
            lag_lower_bound_seconds=lower,
            exceeds_3h=lower > CLAIM_SECONDS,
            reflected_in={
                **where(reading),
                "value_minor": value,
                "change_minor": value - base_value,
            },
        )
    elif last_unreflected is None:
        end = at
        row.update(result="BLOCKED", reason="no valid reading after the event")
    else:
        end = last_unreflected
        covered = seconds(last_unreflected - at)
        if covered <= CLAIM_SECONDS:
            row.update(
                result="BLOCKED",
                reason=f"valid readings end {covered} s after the event, within 3 hours",
            )
        else:
            row.update(result="NOT_REFLECTED", observed_until_seconds=covered, exceeds_3h=True)
    row["overlapping_events"] = [label(o) for o in overlapping(event, events, base_time, end)]
    # An opposite event up to 3 hours earlier may still have been landing in this window.
    pending_from = min(base_time, at - timedelta(seconds=CLAIM_SECONDS))
    opposite = [
        other
        for other in overlapping(event, events, pending_from, end)
        if effect(other) == (balance, -direction)
    ]
    if row["result"] == "NOT_REFLECTED" and opposite:
        row.update(
            result="BLOCKED",
            reason="an overlapping opposite event on the same balance may have cancelled it",
            possible_cancelling_events=[label(other) for other in opposite],
            exceeds_3h=None,
        )
    return row


def reflection(readings: list[tuple[datetime, dict]], events: list[dict]) -> dict:
    rows = [reflection_row(event, readings, events) for event in events if effect(event)]
    measured = [row for row in rows if row["result"] != "BLOCKED"]
    late = [row for row in measured if row.get("exceeds_3h")]
    if not measured:
        verdict = "BLOCKED"
    else:
        verdict = "DIFFERENT" if late else "CONFIRMED"
    by_type: dict[str, dict] = {}
    for row in rows:
        summary = by_type.setdefault(
            row["event"],
            {"REFLECTED": 0, "NOT_REFLECTED": 0, "BLOCKED": 0, "max_lag_seconds": None},
        )
        summary[row["result"]] += 1
        if row["result"] == "REFLECTED":
            summary["max_lag_seconds"] = max(summary["max_lag_seconds"] or 0, row["lag_seconds"])
            summary.setdefault("first_reflected_in", row["reflected_in"])
    return {
        "verdict": verdict,
        "events": len(rows),
        "measured": len(measured),
        "over_3h": len(late),
        "overlapping": sum(bool(row["overlapping_events"]) for row in rows),
        "by_event_type": by_type,
        "rows": rows,
    }


# Part 4: Transaction Search lag for the earliest capture.


def capture_event(events: list[dict]) -> dict | None:
    return next(
        (
            event
            for event in events
            if event.get("event") == "CAPTURE_COMPLETED"
            and parse_time(event.get("at"))
            and isinstance(event.get("resource_id"), str)
            and RESOURCE_ID.fullmatch(event["resource_id"])
        ),
        None,
    )


async def search_once(ctx: CheckContext, capture: dict) -> dict:
    at = parse_time(capture["at"])
    start = at - TXN_LOOKBACK
    end = min(max(ctx.clock(), at), start + TXN_MAX_RANGE)
    poll = {"at": now(ctx), "start_date": search_time(start), "end_date": search_time(end)}
    try:
        response = await ctx.client.request(
            "GET",
            "/v1/reporting/transactions",
            step="transaction_search",
            params={
                "transaction_id": capture["resource_id"],
                "start_date": poll["start_date"],
                "end_date": poll["end_date"],
                "fields": "transaction_info",
            },
        )
    except SandboxRequestError as error:
        return {**poll, "status": None, "error": type(error).__name__, "found": None}
    data = payload(response)
    if response.status_code != 200:
        return {**poll, "status": response.status_code, "error": data.get("name"), "found": None}
    details = data.get("transaction_details")
    infos = [
        detail["transaction_info"]
        for detail in (details if isinstance(details, list) else [])
        if isinstance(detail, dict) and isinstance(detail.get("transaction_info"), dict)
    ]
    match = next(
        (info for info in infos if info.get("transaction_id") == capture["resource_id"]), None
    )
    return {
        **poll,
        "status": 200,
        "found": match is not None,
        "last_refreshed_datetime": data.get("last_refreshed_datetime"),
        "transaction_status": (match or {}).get("transaction_status"),
        "transaction_initiation_date": (match or {}).get("transaction_initiation_date"),
        "transaction_updated_date": (match or {}).get("transaction_updated_date"),
    }


async def fresh_balance(ctx: CheckContext) -> dict:
    """One reading of our own, so every run has at least one."""
    return {"fresh_balance": await balance_snapshot(ctx, step="fresh_balance")}


async def poll_transactions(ctx: CheckContext) -> dict:
    """Search for the capture every 5 minutes for up to 3 hours; resumes where it stopped."""
    capture = ctx.data.get("transactions_capture")
    if capture is None:
        event = capture_event(timeline.load_all(ctx.root))
        capture = (
            {key: event.get(key) for key in ("source", "resource_id", "at", "paypal_time")}
            if event
            else {}
        )
        ctx.checkpoint(transactions_capture=capture)
    polls = list(ctx.data.get("transaction_polls") or [])
    if not capture:
        ctx.note("D12: no CAPTURE_COMPLETED event in the timeline; transactions lag BLOCKED.")
        return {"transaction_polls": polls}
    total = TXN_OBSERVE_SECONDS // TXN_INTERVAL_SECONDS + 1
    while len(polls) < total and not any(poll.get("found") for poll in polls):
        if polls:
            await ctx.sleep(TXN_INTERVAL_SECONDS)
        polls.append(await search_once(ctx, capture))
        ctx.checkpoint(transaction_polls=polls)
    return {"transaction_polls": polls}


async def wait_for_settlement(ctx: CheckContext) -> dict:
    """Keep running (and the runner's balance poller with it) until D10 has finished.

    The transactions lag must start within hours of D2, but the balance table needs every
    event up to D10, so one run spans the day instead of a second run overwriting the first.
    """
    await ctx.manual.confirm(
        "D12: Transaction Search lag recorded. Leave this window open: the balance poller keeps "
        "a reading every 5 minutes. Run D3 to D10 in another terminal, then press Enter here "
        "once D10 has finished to build the balance table."
    )
    return {"analysis_started_at": now(ctx)}


def transactions_result(ctx: CheckContext) -> dict:
    capture = ctx.data.get("transactions_capture") or {}
    polls = ctx.data.get("transaction_polls") or []
    if not capture:
        return {
            "verdict": "BLOCKED",
            "reason": "no CAPTURE_COMPLETED event in the D2 to D10 timeline",
        }
    at = parse_time(capture["at"])
    valid = [poll for poll in polls if poll.get("found") is not None]
    first = next((poll for poll in valid if poll["found"]), None)
    misses = [poll for poll in valid if not poll["found"]]
    last_miss = seconds(parse_time(misses[-1]["at"]) - at) if misses else None
    result = {
        "capture": capture,
        "polls": len(polls),
        "valid_polls": len(valid),
        "failed_polls": len(polls) - len(valid),
        "first_seen_at": first and first["at"],
        "first_seen_poll": polls.index(first) + 1 if first else None,
        "seconds_after_event": seconds(parse_time(first["at"]) - at) if first else None,
        "not_seen_until_seconds": last_miss,
        "upper_bound": bool(first) and not misses,
        "first_seen": first,
    }
    paypal_time = parse_time(capture.get("paypal_time"))
    if first and paypal_time:
        result["seconds_after_paypal_time"] = seconds(parse_time(first["at"]) - paypal_time)
    if last_miss is not None and last_miss > CLAIM_SECONDS:
        result["verdict"] = "DIFFERENT"
    elif first and result["seconds_after_event"] <= CLAIM_SECONDS:
        result["verdict"] = "CONFIRMED"
    elif first:
        result.update(
            verdict="BLOCKED",
            reason="first seen more than 3 hours after the capture with no earlier miss "
            "beyond 3 hours, so the lag is not pinned down",
        )
    else:
        result.update(verdict="BLOCKED", reason="no successful Transaction Search call")
    return result


async def findings(ctx: CheckContext) -> dict:
    events = timeline.load_all(ctx.root)
    readings, malformed = collect_readings(ctx)
    valid = [(fetched, reading) for fetched, reading in readings if is_valid(reading)]
    parts = {
        "freshness": freshness(valid),
        "consistency": consistency(valid),
        "reflection": reflection(readings, events),
        "transactions": transactions_result(ctx),
    }
    verdicts = {name: part["verdict"] for name, part in parts.items()}
    if "DIFFERENT" in verdicts.values():
        verdict = "DIFFERENT"
    elif not valid or verdicts["freshness"] == "BLOCKED":
        verdict = "BLOCKED"
    else:
        verdict = "CONFIRMED"
    sources: dict[str, int] = {}
    for _, reading in readings:
        name = reading["source"].split(":")[0].split("[")[0]
        sources[name] = sources.get(name, 0) + 1
    return write_findings(
        ctx,
        {
            "check_id": "D12",
            "verdict": verdict,
            "claim": "balances can lag up to 3 hours",
            "part_verdicts": verdicts,
            "blocked_parts": sorted(name for name, v in verdicts.items() if v == "BLOCKED"),
            "readings": {
                "total": len(readings),
                "valid": len(valid),
                "invalid": len(readings) - len(valid),
                "malformed_lines": malformed,
                "by_source": sources,
                "first_fetched_at": readings[0][1]["fetched_at"] if readings else None,
                "last_fetched_at": readings[-1][1]["fetched_at"] if readings else None,
            },
            "timeline_events": len(events),
            **parts,
            "notes": list(NOTES),
        },
    )


def get_steps() -> list[Step]:
    return [
        Step("fresh_balance", fresh_balance),
        Step("poll_transactions", poll_transactions),
        Step("wait_for_settlement", wait_for_settlement),
        Step("findings", findings),
    ]
