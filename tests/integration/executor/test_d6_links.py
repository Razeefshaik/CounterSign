"""The recorded D6 duplicate shape, with all HTTP served in process."""

import json

import httpx
import pytest
import test_executor as executor_tests
from test_executor import approved, items, rows

from countersign.config import PayPalSettings
from countersign.executor import Executor
from countersign.paypal.http import PayPalHTTP

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
BASE = "https://api-m.sandbox.paypal.com"
world = executor_tests.world


@pytest.mark.parametrize("mismatch", [None, "receiver", "amount", "sender_item_id"])
async def test_unknown_uses_nested_alias_link_only_for_identical_batch(world, mismatch):
    database, _, _, controls, clock = world
    batches, hosts = {}, []

    async def respond(request):
        hosts.append(request.url.host)
        if request.method == "POST" and request.url.path == "/v1/payments/payouts":
            key = json.loads(request.content)["sender_batch_header"]["sender_batch_id"]
            if key in batches:
                return httpx.Response(
                    400,
                    json={
                        "name": "USER_BUSINESS_ERROR",
                        "message": "User business error.",
                        "details": [
                            {
                                "field": "SENDER_BATCH_ID",
                                "location": "body",
                                "issue": "Batch with given sender_batch_id already exists",
                                "link": [
                                    {
                                        "href": "https://api.sandbox.paypal.com"
                                        + "/v1/payments/payouts/"
                                        + batches[key],
                                        "rel": "self",
                                        "method": "GET",
                                    }
                                ],
                            }
                        ],
                        "links": [],
                    },
                )
            response = await controls.counting.inner.handle_async_request(request)
            await response.aread()
            batches[key] = response.json()["batch_header"]["payout_batch_id"]
            raise httpx.ReadTimeout("lost response", request=request)
        response = await controls.counting.inner.handle_async_request(request)
        if request.method == "GET" and request.url.path.startswith("/v1/payments/payouts/"):
            await response.aread()
            body = response.json()
            if mismatch:
                item = body["items"][0]["payout_item"]
                if mismatch == "amount":
                    item["amount"]["value"] = "999.00"
                elif mismatch == "sender_item_id":
                    body["items"][0]["sender_item_id"] = "DIFFERENT"
                    item["sender_item_id"] = "DIFFERENT"
                else:
                    item["receiver"] = "different@example.test"
                return httpx.Response(200, json=body)
        return response

    async with PayPalHTTP(
        PayPalSettings(base_url=BASE), transport=httpx.MockTransport(respond)
    ) as http:
        executor = Executor.from_http(database, http, clock=clock)
        _, proposal_id = await approved(world)
        first = await executor.execute(proposal_id)
        assert set(first["operations"].values()) == {"UNKNOWN"}
        swept = await executor.sweep()
        expected = "UNKNOWN" if mismatch else "SUCCEEDED"
        assert set(swept["resolved"].values()) == {expected}
    assert len(await items(controls)) == 2
    assert {a["state"] for a in await rows(database, "payout_attempts")} == {
        "UNKNOWN" if mismatch else "SUBMITTED"
    }
    assert set(hosts) == {"api-m.sandbox.paypal.com"}
