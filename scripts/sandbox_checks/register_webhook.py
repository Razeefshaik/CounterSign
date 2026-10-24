"""Register the catcher's tunnel URL as a sandbox webhook (S3). Prints only the new id.

Run: python -m scripts.sandbox_checks.register_webhook --url https://<tunnel>/webhooks/paypal
Copy the printed id into PAYPAL_WEBHOOK_ID in .env by hand.
After a tunnel restart, add --update to move the existing webhook to the new URL (same id).
"""

import argparse
import asyncio
import json
import sys
from urllib.parse import urlsplit

from .client import SandboxClient
from .env import SandboxSettings, load_settings
from .recorder import Recorder
from .redact import Redactor

# Named one by one: PayPal accepts "*" for all events, not partial wildcards.
EVENT_TYPES = (
    *(
        f"PAYMENT.PAYOUTS-ITEM.{suffix}"
        for suffix in (
            "BLOCKED",
            "CANCELED",
            "DENIED",
            "FAILED",
            "HELD",
            "REFUNDED",
            "RETURNED",
            "SUCCEEDED",
            "UNCLAIMED",
        )
    ),
    *(f"PAYMENT.PAYOUTSBATCH.{suffix}" for suffix in ("DENIED", "PROCESSING", "SUCCESS")),
    *(f"CUSTOMER.DISPUTE.{suffix}" for suffix in ("CREATED", "UPDATED", "RESOLVED")),
    *(
        f"PAYMENT.CAPTURE.{suffix}"
        for suffix in ("COMPLETED", "DENIED", "PENDING", "REFUNDED", "REVERSED")
    ),
)


def registration_body(url: str) -> dict:
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.netloc:
        raise ValueError("Webhook URL must be an absolute https:// URL.")
    return {"url": url, "event_types": [{"name": name} for name in EVENT_TYPES]}


async def register(
    url: str, settings: SandboxSettings, recorder: Recorder, *, transport=None
) -> str:
    """Return the new webhook id; raise RuntimeError with PayPal's error name only."""
    body = registration_body(url)
    async with SandboxClient(settings, recorder, transport=transport) as client:
        response = await client.request(
            "POST", "/v1/notifications/webhooks", step="register_webhook", json=body
        )
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if response.status_code != 201 or not isinstance(payload.get("id"), str):
        name = payload.get("name", "UNKNOWN") if isinstance(payload, dict) else "UNKNOWN"
        raise RuntimeError(f"Webhook registration failed (HTTP {response.status_code}, {name}).")
    return payload["id"]


async def update_url(
    url: str, settings: SandboxSettings, recorder: Recorder, *, transport=None
) -> str:
    """Point the registered webhook (PAYPAL_WEBHOOK_ID) at a new tunnel URL; keep its id."""
    registration_body(url)
    if not settings.webhook_id:
        raise RuntimeError("PAYPAL_WEBHOOK_ID is not set.")
    patch = [{"op": "replace", "path": "/url", "value": url}]
    async with SandboxClient(settings, recorder, transport=transport) as client:
        response = await client.request(
            "PATCH",
            f"/v1/notifications/webhooks/{settings.webhook_id}",
            step="update_webhook_url",
            json=patch,
        )
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if response.status_code != 200 or not isinstance(payload, dict) or payload.get("url") != url:
        name = payload.get("name", "UNKNOWN") if isinstance(payload, dict) else "UNKNOWN"
        raise RuntimeError(f"Webhook URL update failed (HTTP {response.status_code}, {name}).")
    return settings.webhook_id


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", required=True, help="public https URL ending in /webhooks/paypal")
    parser.add_argument("--dry-run", action="store_true", help="print the request body only")
    parser.add_argument(
        "--update", action="store_true", help="move the existing webhook to --url (new tunnel)"
    )
    args = parser.parse_args(argv)
    try:
        body = registration_body(args.url)
    except ValueError as error:
        print(error, file=sys.stderr)
        return 2
    if args.dry_run:
        print(json.dumps(body, indent=2))
        return 0
    settings = load_settings()
    recorder = Recorder("D11", redactor=Redactor.from_settings(settings))
    action = update_url if args.update else register
    try:
        webhook_id = asyncio.run(action(args.url, settings, recorder))
    except RuntimeError as error:
        print(error, file=sys.stderr)
        return 1
    print(webhook_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
