"""GET-only resource reader. Raw PayPal statuses are retained, never guessed."""

from dataclasses import replace

from countersign.paypal.http import PayPalHTTP, parse_amounts, resource_id


class PayPalRead:
    def __init__(self, http: PayPalHTTP):
        self.http = http

    async def _get(self, path: str, params=None):
        result = await self.http.request("GET", path, params=params)
        if 200 <= result.status_code < 300:
            result = replace(result, amounts=parse_amounts(result.body))
        return result

    async def get_payout_batch(self, batch_id: str, *, page: int = 1, page_size: int = 100):
        return await self._get(
            f"/v1/payments/payouts/{resource_id(batch_id)}",
            {"page": page, "page_size": page_size, "total_required": "true"},
        )

    async def get_payout_item(self, item_id: str):
        return await self._get(f"/v1/payments/payouts-item/{resource_id(item_id)}")

    async def list_disputes(self, **params):
        return await self._get("/v1/customer/disputes", params)

    async def get_dispute(self, dispute_id: str):
        return await self._get(f"/v1/customer/disputes/{resource_id(dispute_id)}")

    async def get_balances(self, **params):
        return await self._get("/v1/reporting/balances", params)

    async def get_trackers(self, transaction_id: str):
        return await self._get(
            "/v1/shipping/trackers", {"transaction_id": resource_id(transaction_id)}
        )

    async def get_capture(self, capture_id: str):
        return await self._get(f"/v2/payments/captures/{resource_id(capture_id)}")

    async def get_order(self, order_id: str):
        return await self._get(f"/v2/checkout/orders/{resource_id(order_id)}")

    async def list_webhook_events(self, **params):
        return await self._get("/v1/notifications/webhooks-events", params)

    async def get_webhook_event(self, event_id: str):
        return await self._get(f"/v1/notifications/webhooks-events/{resource_id(event_id)}")

    async def follow(self, href: str):
        """Only a same-origin resource link can be followed, always with GET."""
        return await self._get(self.http.url(href))
