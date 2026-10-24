from copy import deepcopy

import httpx
import pytest
from test_adapter import operation

from countersign.config import PayPalSettings
from countersign.paypal.http import Ambiguous, PayPalHTTP, UnsafePayPalURL
from countersign.paypal.write import PayPalWrite

SANDBOX = "https://api-m.sandbox.paypal.com"
ALIAS = "https://api.sandbox.paypal.com"


def duplicate(href=ALIAS + "/v1/payments/payouts/ORIGINAL"):
    return {
        "name": "USER_BUSINESS_ERROR",
        "message": "User business error.",
        "details": [
            {
                "field": "SENDER_BATCH_ID",
                "location": "body",
                "issue": "Batch with given sender_batch_id already exists",
                "link": [{"href": href, "rel": "self", "method": "GET"}],
            }
        ],
        "links": [],
    }


@pytest.mark.asyncio
async def test_nested_duplicate_is_ambiguous_on_first_send_and_replay():
    seen = []

    def respond(request):
        seen.append(request)
        if request.url.path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "FAKE-TOKEN", "expires_in": 300})
        return httpx.Response(400, json=duplicate())

    async with PayPalHTTP(
        PayPalSettings(base_url=SANDBOX), transport=httpx.MockTransport(respond)
    ) as http:
        writer = PayPalWrite(http)
        for state in ("IN_FLIGHT", "UNKNOWN"):
            result = await writer.create_payout_single_item(operation(state=state))
            assert isinstance(result, Ambiguous)
            assert result.original_batch_link == SANDBOX + "/v1/payments/payouts/ORIGINAL"
        assert all(request.url.host == "api-m.sandbox.paypal.com" for request in seen)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "href",
    [
        "https://evil.example/v1/payments/payouts/ORIGINAL",
        ALIAS + ":444/v1/payments/payouts/ORIGINAL",
        ALIAS + ":0/v1/payments/payouts/ORIGINAL",
        "http://api.sandbox.paypal.com/v1/payments/payouts/ORIGINAL",
        ALIAS + "/v1/payments/payouts-item/ITEM/cancel",
        ALIAS + "/v1/payments/payouts/ORIGINAL?token=private",
        ALIAS + "/v1/payments/payouts/ORIGINAL#fragment",
        "https://user:pass@api.sandbox.paypal.com/v1/payments/payouts/ORIGINAL",
    ],
)
async def test_unsafe_nested_batch_link_is_ignored(href):
    def respond(request):
        if request.url.path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "FAKE-TOKEN", "expires_in": 300})
        return httpx.Response(400, json=duplicate(href))

    async with PayPalHTTP(PayPalSettings(base_url=SANDBOX),
                          transport=httpx.MockTransport(respond)) as http:
        assert PayPalWrite(http)._original_batch_link(duplicate(href)) is None
        result = await PayPalWrite(http).create_payout_single_item(operation())
        assert isinstance(result, Ambiguous) and result.original_batch_link is None


@pytest.mark.asyncio
async def test_alias_is_one_way_and_only_for_configured_sandbox():
    async with PayPalHTTP(PayPalSettings(base_url=SANDBOX)) as http:
        assert http.url(ALIAS + ":443/v1/payments/payouts/ORIGINAL") == (
            SANDBOX + "/v1/payments/payouts/ORIGINAL"
        )
    async with PayPalHTTP(PayPalSettings()) as http:
        with pytest.raises(UnsafePayPalURL):
            http.url(ALIAS + "/v1/payments/payouts/ORIGINAL")


@pytest.mark.asyncio
async def test_malformed_nested_links_do_not_hide_a_valid_top_level_link():
    body = deepcopy(duplicate())
    body["details"] = [{"link": "bad"}, None]
    body["links"] = [{"rel": "self", "href": "/v1/payments/payouts/TOP"}]
    async with PayPalHTTP(PayPalSettings(base_url=SANDBOX)) as http:
        assert PayPalWrite(http)._original_batch_link(body).endswith("/TOP")
