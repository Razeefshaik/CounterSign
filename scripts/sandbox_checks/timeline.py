"""Shared check timeline: what each check observed, for D11 webhook and D12 balance analysis.

Checks D2 to D10 call record(ctx, ...) when they observe a PayPal state change. D11 matches
the events to webhook deliveries; D12 matches them to balance snapshots.
"""

from pathlib import Path

from .recorder import timestamp
from .runner import CheckContext, load_check_data

# event -> (webhook event types that should announce it, balance effect once PayPal reflects it)
# A balance effect is (balance, direction); None means no balance change is expected.
EVENTS = {
    "CAPTURE_COMPLETED": (("PAYMENT.CAPTURE.COMPLETED",), ("available", 1)),
    "CAPTURE_REFUNDED": (("PAYMENT.CAPTURE.REFUNDED",), ("available", -1)),
    "PAYOUT_SUBMITTED": (
        ("PAYMENT.PAYOUTSBATCH.PROCESSING", "PAYMENT.PAYOUTSBATCH.SUCCESS"),
        ("available", -1),
    ),
    "PAYOUT_ITEM_SUCCESS": (("PAYMENT.PAYOUTS-ITEM.SUCCEEDED",), None),
    "PAYOUT_ITEM_UNCLAIMED": (("PAYMENT.PAYOUTS-ITEM.UNCLAIMED",), None),
    "PAYOUT_ITEM_FAILED": (("PAYMENT.PAYOUTS-ITEM.FAILED",), ("available", 1)),
    "PAYOUT_ITEM_RETURNED": (
        ("PAYMENT.PAYOUTS-ITEM.RETURNED", "PAYMENT.PAYOUTS-ITEM.CANCELED"),
        ("available", 1),
    ),
    "DISPUTE_CREATED": (("CUSTOMER.DISPUTE.CREATED",), ("withheld", 1)),
    "DISPUTE_UPDATED": (("CUSTOMER.DISPUTE.UPDATED",), None),
    "DISPUTE_RESOLVED": (("CUSTOMER.DISPUTE.RESOLVED",), ("withheld", -1)),
}
SOURCES = tuple(f"D{number}" for number in range(2, 11))


def record(
    ctx: CheckContext,
    event: str,
    *,
    resource_id: str,
    amount_minor: int | None = None,
    currency: str | None = None,
    paypal_time: str | None = None,
    at: str | None = None,
) -> dict:
    """Append one observation and checkpoint it; a repeat on resume returns the saved entry.

    at is when the check observed the change (defaults to now). paypal_time is PayPal's own
    timestamp for it when the response carries one (time_processed, update_time, ...).
    """
    if event not in EVENTS:
        raise ValueError("Unknown timeline event.")
    timeline = list(ctx.data.get("timeline") or [])
    for entry in timeline:
        if entry["event"] == event and entry["resource_id"] == resource_id:
            return entry
    entry = {
        "at": at or timestamp(ctx.clock()),
        "source": ctx.state["check_id"],
        "event": event,
        "resource_id": resource_id,
        "amount_minor": amount_minor,
        "currency": currency,
        "paypal_time": paypal_time,
    }
    timeline.append(entry)
    ctx.checkpoint(timeline=timeline)
    return entry


def load_all(root: str | Path) -> list[dict]:
    """Every saved event from D2 to D10, oldest first."""
    events = [
        entry
        for check_id in SOURCES
        for entry in load_check_data(root, check_id).get("timeline") or []
    ]
    return sorted(events, key=lambda entry: entry["at"])
