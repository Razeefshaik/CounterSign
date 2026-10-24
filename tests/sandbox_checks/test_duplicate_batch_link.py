import pytest

from scripts.sandbox_checks.checks.payout_common import batch_link


@pytest.mark.parametrize("origin", ["https://api-m.sandbox.paypal.com", "https://api.sandbox.paypal.com", "https://api.sandbox.paypal.com:443", ""])
def test_nested_sandbox_original_batch_link(origin):
    body = {"links": [], "details": [{"link": [{"method": "GET", "href": origin + "/v1/payments/payouts/ORIGINAL"}]}]}
    assert batch_link(body) == "ORIGINAL"


@pytest.mark.parametrize("href", [
    "https://evil.example/v1/payments/payouts/ORIGINAL",
    "https://api.sandbox.paypal.com:444/v1/payments/payouts/ORIGINAL",
    "https://user:pass@api.sandbox.paypal.com/v1/payments/payouts/ORIGINAL",
    "/v1/payments/payouts/ORIGINAL?secret=value", "/v1/payments/payouts/ORIGINAL#fragment",
    "/v1/payments/payouts-item/ITEM/cancel", "//api.sandbox.paypal.com/v1/payments/payouts/ORIGINAL",
])
def test_unsafe_harness_link_is_ignored(href):
    assert batch_link({"details": [{"link": [{"href": href}]}]}) is None
