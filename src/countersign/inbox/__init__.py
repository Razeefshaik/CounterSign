"""Durable webhook ingestion and GET-confirmed processing, without PayPal writes."""

from .service import Inbox, InboxError

__all__ = ["Inbox", "InboxError"]
