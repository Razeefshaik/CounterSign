"""Executor-only writes from persisted operation payloads, with no automatic retry.

The executor commits the operation IN_FLIGHT before calling these methods. The
adapter verifies its canonical payload fingerprint, but cannot itself prove a
database commit: that is the executor's write-ahead responsibility.
"""

import hashlib
import hmac
import json
from collections.abc import Mapping
from urllib.parse import urlsplit
from uuid import UUID

from countersign.money import Money, MoneyError
from countersign.paypal.http import (
    Ambiguous,
    DefiniteError,
    Ok,
    PayPalAuthError,
    PayPalDataError,
    PayPalHTTP,
    PayPalTransportError,
    UnsafePayPalURL,
    canonical_payload,
    resource_id,
)


class InvalidOperation(PayPalDataError):
    pass


def _persisted(operation: Mapping, kind: str):
    try:
        UUID(str(operation["id"]))
        valid = operation["kind"] == kind and operation["state"] in {"IN_FLIGHT", "UNKNOWN"}
        payload = operation["payload"]
        valid = valid and isinstance(payload, Mapping) and isinstance(payload.get("body"), dict)
        encoded = canonical_payload(payload)
        recorded = operation["payload_hash"]
        if isinstance(recorded, str) and recorded.startswith("sha256:"):
            recorded = bytes.fromhex(recorded[7:])
        recorded = bytes(recorded)
        valid = valid and hmac.compare_digest(hashlib.sha256(encoded).digest(), recorded)
        payload = json.loads(encoded)  # Stable snapshot even while OAuth awaits a response.
        request_id = resource_id(payload["request_id"])
    except (KeyError, TypeError, ValueError, AttributeError, PayPalDataError):
        valid = False
    if not valid:
        raise InvalidOperation(
            "Persisted operation kind, state, payload or fingerprint is invalid"
        ) from None
    return payload, request_id


def _names(body) -> set[str]:
    if not isinstance(body, Mapping):
        return set()
    values = [body.get("name"), body.get("issue")]
    for detail in body.get("details", []) if isinstance(body.get("details", []), list) else []:
        if isinstance(detail, Mapping):
            values.extend((detail.get("issue"), detail.get("name")))
    return {value for value in values if isinstance(value, str)}


class PayPalWrite:
    def __init__(self, http: PayPalHTTP):
        self.http = http

    def _original_batch_link(self, body):
        if not isinstance(body, Mapping):
            return None
        links = body.get("links", [])
        if not isinstance(links, list):
            return None
        for link in links:
            if not isinstance(link, Mapping) or link.get("rel") not in {
                "original_batch",
                "original-batch",
                "original",
                "self",
            }:
                continue
            href = link.get("href")
            try:
                validated = self.http.url(href)
            except UnsafePayPalURL:
                continue
            # Only a batch-resource GET is reconciliation evidence, never an action URL.
            path = urlsplit(validated).path
            prefix = "/v1/payments/payouts/"
            if (
                path.startswith(prefix)
                and "/" not in path[len(prefix) :]
                and path[len(prefix) :]
                and self.http.redactor.text(validated) == validated
            ):
                # Original-batch identity is the path. Do not carry arbitrary query data
                # from a server response into logs or a subsequent authenticated GET.
                return validated.partition("?")[0]
        return None

    async def _write(self, operation, kind, path, *, body_format="json", replay_unknown=False):
        payload, request_id = _persisted(operation, kind)
        if self.http.redactor.text(request_id) != request_id:
            raise InvalidOperation("Operation request ID cannot contain private values")
        body = payload["body"]
        unknown = replay_unknown or operation["state"] == "UNKNOWN"
        kwargs = {"json": body}
        if body_format == "multipart":
            kwargs = {"files": {"input": (None, canonical_payload(body), "application/json")}}
        try:
            response = await self.http.request("POST", path, request_id=request_id, **kwargs)
        except (PayPalTransportError, PayPalAuthError) as exc:
            return Ambiguous(type(exc).__name__, request_id=request_id)
        if 200 <= response.status_code < 300:
            # A malformed success response cannot identify a payout accepted remotely.
            if response.body is None and response.status_code != 204:
                return Ambiguous("Malformed successful response", response, request_id)
            return Ok(response)
        names = _names(response.body)
        original = self._original_batch_link(response.body)
        behavior = self.http.settings.behavior
        if (
            unknown
            or response.status_code >= 500
            or not 400 <= response.status_code < 500
            or names & behavior.ambiguous_error_names
            or (
                behavior.reconcile_original_batch_links
                and original
                and "USER_BUSINESS_ERROR" in names
            )
        ):
            return Ambiguous("Reconciliation required", response, request_id, original)
        return DefiniteError(response)

    async def create_payout_single_item(self, operation: Mapping, *, replay_unknown=False):
        payload, request_id = _persisted(operation, "PAYOUT_CREATE")
        try:
            body = payload["body"]
            items = body["items"]
            valid = (
                isinstance(items, list)
                and len(items) == 1
                and body["sender_batch_header"]["sender_batch_id"] == request_id
                and items[0]["sender_item_id"] == request_id + "-i1"
                and items[0]["recipient_type"] == "EMAIL"
                and isinstance(items[0]["receiver"], str)
                and Money.from_paypal(items[0]["amount"]).minor > 0
            )
        except (KeyError, TypeError, MoneyError):
            valid = False
        if not valid:
            raise InvalidOperation(
                "Payout must contain one item with persisted keys and exact money"
            )
        return await self._write(
            operation, "PAYOUT_CREATE", "/v1/payments/payouts", replay_unknown=replay_unknown
        )

    async def cancel_item(self, operation: Mapping, *, replay_unknown=False):
        payload, _ = _persisted(operation, "PAYOUT_CANCEL")
        target = resource_id(payload.get("payout_item_id"))
        return await self._write(
            operation,
            "PAYOUT_CANCEL",
            f"/v1/payments/payouts-item/{target}/cancel",
            replay_unknown=replay_unknown,
        )

    async def provide_evidence(self, operation: Mapping, *, replay_unknown=False):
        payload, _ = _persisted(operation, "DISPUTE_EVIDENCE")
        target = resource_id(payload.get("paypal_dispute_id"))
        return await self._write(
            operation,
            "DISPUTE_EVIDENCE",
            f"/v1/customer/disputes/{target}/provide-evidence",
            body_format=self.http.settings.behavior.evidence_format,
            replay_unknown=replay_unknown,
        )

    async def accept_claim(self, operation: Mapping, *, replay_unknown=False):
        payload, _ = _persisted(operation, "DISPUTE_ACCEPT_CLAIM")
        target = resource_id(payload.get("paypal_dispute_id"))
        return await self._write(
            operation,
            "DISPUTE_ACCEPT_CLAIM",
            f"/v1/customer/disputes/{target}/accept-claim",
            body_format=self.http.settings.behavior.accept_claim_format,
            replay_unknown=replay_unknown,
        )
