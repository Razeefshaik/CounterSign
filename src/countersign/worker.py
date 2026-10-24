"""A retry loop over the PostgreSQL inbox; no external queue or PayPal writes."""

import asyncio
import logging

logger = logging.getLogger(__name__)


async def run_inbox(inbox, stop: asyncio.Event, *, interval: float = 5.0):
    while not stop.is_set():
        try:
            await inbox.process_one()
        except Exception:  # noqa: BLE001 - retain pending work without private exception text.
            logger.warning("Webhook worker unavailable; durable work remains pending")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            pass
