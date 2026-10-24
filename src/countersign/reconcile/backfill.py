"""Optional GET-only hints from PayPal's event history [pending D11].

History supplies resource identifiers, not authenticated webhook bytes. Every hint
is confirmed with a fresh resource GET; no signature SUCCESS is ever manufactured.
"""

from collections.abc import Awaitable, Callable
from datetime import datetime

from countersign.db import Repositories
from countersign.paypal.http import resource_id

from .common import ReconcileError, StageResult

ResourceCallback = Callable[[str, str, dict], Awaitable[StageResult]]
MAX_PAGES = 100


def _hint(event):
    kind = event.get("event_type")
    resource = event.get("resource")
    if not isinstance(kind, str) or not isinstance(resource, dict):
        raise TypeError
    if kind.startswith("CUSTOMER.DISPUTE."):
        return "dispute", resource_id(resource.get("dispute_id") or resource.get("id"))
    if kind.startswith("PAYMENT.PAYOUTS-ITEM."):
        return "payout_item", resource_id(resource.get("payout_item_id"))
    if kind.startswith("PAYMENT.PAYOUTSBATCH."):
        header = resource.get("batch_header") or {}
        if not isinstance(header, dict):
            raise ValueError
        return "payout_batch", resource_id(
            resource.get("payout_batch_id") or header.get("payout_batch_id")
        )
    if kind.startswith("PAYMENT.CAPTURE."):
        return "capture", resource_id(resource.get("id"))
    return None


def _page(response):
    if not 200 <= response.status_code < 300 or not isinstance(response.body, dict):
        raise ValueError
    events, links = response.body.get("events"), response.body.get("links", [])
    if not isinstance(events, list) or not isinstance(links, list):
        raise TypeError
    following = []
    for link in links:
        if not isinstance(link, dict):
            raise TypeError
        if link.get("rel") == "next":
            if link.get("method", "GET").upper() != "GET":
                raise ValueError
            href = link.get("href")
            if not isinstance(href, str) or not href:
                raise ValueError
            following.append(href)
    if len(following) > 1:
        raise ValueError
    return events, following[0] if following else None


def _confirmed_identity(body, kind):
    if kind == "payout_batch":
        header = body.get("batch_header")
        if not isinstance(header, dict):
            raise TypeError
        return resource_id(header.get("payout_batch_id"))
    key = {"dispute": "dispute_id", "payout_item": "payout_item_id", "capture": "id"}[kind]
    return resource_id(body.get(key))


async def backfill_events(
    conn,
    reader,
    *,
    now: datetime,
    enabled: bool = False,
    on_resource: ResourceCallback | None = None,
) -> StageResult:
    """Read history once per event; known resources still have their normal polls.

    The caller owns the transaction and global cycle lock. Callback effects and
    checkpoints commit together. A callback may ingest a GET-confirmed dispute;
    payouts remain hints and never resolve an UNKNOWN or PREPARED attempt.
    """
    result = StageResult()
    if not enabled:
        return result
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ReconcileError("Invalid backfill observation")
    repos, visited = Repositories(conn), set()
    try:
        response = await reader.list_webhook_events()
        for _ in range(MAX_PAGES):
            events, following = _page(response)
            for event in events:
                if not isinstance(event, dict):
                    raise TypeError
                event_id = resource_id(event.get("id"))
                result.seen += 1
                key = "reconcile.backfill:" + event_id
                if await repos.policies.get(key):
                    result.skipped += 1
                    continue
                hint = _hint(event)
                if hint is None:
                    result.skipped += 1
                    continue
                kind, identifier = hint
                getter = getattr(
                    reader,
                    {
                        "dispute": "get_dispute",
                        "payout_item": "get_payout_item",
                        "payout_batch": "get_payout_batch",
                        "capture": "get_capture",
                    }[kind],
                )
                detail = await getter(identifier)
                if not 200 <= detail.status_code < 300 or not isinstance(detail.body, dict):
                    raise ValueError
                if _confirmed_identity(detail.body, kind) != identifier:
                    raise ValueError
                if on_resource is not None:
                    observed = await on_resource(kind, identifier, detail.body)
                    if observed.errors:
                        raise ReconcileError("Backfill resource confirmation failed")
                    for name in (
                        "updated",
                        "skipped",
                        "incidents",
                        "errors",
                        "unknown",
                        "prepared",
                    ):
                        setattr(result, name, getattr(result, name) + getattr(observed, name))
                await repos.policies.insert(
                    key=key,
                    value={"kind": kind, "resource_id": identifier, "checked_at": now.isoformat()},
                )
                result.updated += 1
            if following is None:
                return result
            if following in visited:
                raise ValueError
            visited.add(following)
            # PayPalRead.follow rejects cross-origin URLs and always uses GET.
            response = await reader.follow(following)
        raise ValueError
    except ReconcileError:
        raise
    except Exception:  # noqa: BLE001 -- callback/transport failures must expose only fixed text.
        raise ReconcileError("Backfill resource confirmation failed") from None
