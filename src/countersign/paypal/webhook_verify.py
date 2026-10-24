"""Postback signature verification; the inbox owns durable exact-byte storage."""

import json
from collections.abc import Mapping
from enum import StrEnum

from countersign.paypal.http import PayPalAuthError, PayPalHTTP, PayPalTransportError


class Verification(StrEnum):
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    UNKNOWN = "UNKNOWN"


_FIELDS = {
    "auth_algo": "paypal-auth-algo",
    "cert_url": "paypal-cert-url",
    "transmission_id": "paypal-transmission-id",
    "transmission_sig": "paypal-transmission-sig",
    "transmission_time": "paypal-transmission-time",
}


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


class WebhookVerifier:
    def __init__(self, http: PayPalHTTP, webhook_id: str | None = None):
        self.http = http
        self._webhook_id = http.settings.webhook_id if webhook_id is None else webhook_id
        http.redactor.add_secret(self._webhook_id)

    def __repr__(self):
        return "WebhookVerifier(values=<REDACTED>)"

    async def verify(self, raw_body: bytes, headers: Mapping[str, str]) -> Verification:
        """Do not reserialize before saving. Postback requires the parsed event object.

        We never fetch the certificate URL: PayPal verifies it. No unknown outcome
        can turn into FAILURE, and only an explicit SUCCESS releases processing.
        """
        try:
            lowered = {key.lower(): value for key, value in headers.items()}
            body = {key: lowered.get(header, "") for key, header in _FIELDS.items()}
            if not self._webhook_id or not all(body.values()):
                return Verification.UNKNOWN
            if not isinstance(raw_body, bytes):
                return Verification.UNKNOWN
            event = json.loads(raw_body, object_pairs_hook=_object, parse_constant=_bad_constant)
            if not isinstance(event, dict) or not isinstance(event.get("id"), str):
                return Verification.UNKNOWN
            body.update(webhook_id=self._webhook_id, webhook_event=event)
            response = await self.http.request(
                "POST", "/v1/notifications/verify-webhook-signature", json=body
            )
        except (ValueError, UnicodeError, TypeError, PayPalTransportError, PayPalAuthError):
            return Verification.UNKNOWN
        if not 200 <= response.status_code < 300 or not isinstance(response.body, Mapping):
            return Verification.UNKNOWN
        value = response.body.get("verification_status")
        return (
            Verification(value)
            if isinstance(value, str) and value in {"SUCCESS", "FAILURE"}
            else Verification.UNKNOWN
        )


def _bad_constant(value):
    raise ValueError("Non-JSON constant")
