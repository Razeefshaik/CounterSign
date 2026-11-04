"""C65: fixed public route vocabulary; never return caller-supplied path segments."""

import re
from urllib.parse import urlsplit

PRODUCTS = ("Payouts", "Disputes", "Orders", "Payments", "Tracking", "Reporting", "Webhooks", "OAuth")
ROUTES = {
    "Payouts": ("/v1/payments/payouts", "/v1/payments/payouts/{id}",
                "/v1/payments/payouts-item/{id}", "/v1/payments/payouts-item/{id}/cancel"),
    "Disputes": ("/v1/customer/disputes", "/v1/customer/disputes/{id}", *(
        "/v1/customer/disputes/{id}/" + action for action in (
            "provide-evidence", "accept-claim", "escalate", "require-evidence", "adjudicate",
            "send-message", "make-offer", "accept-offer", "appeal", "acknowledge-return-item"))),
    "Orders": ("/v2/checkout/orders", "/v2/checkout/orders/{id}",
               "/v2/checkout/orders/{id}/capture", "/v2/checkout/orders/{id}/authorize"),
    "Payments": ("/v2/payments/captures/{id}", "/v2/payments/captures/{id}/refund",
                 "/v2/payments/refunds/{id}"),
    "Tracking": ("/v1/shipping/trackers", "/v1/shipping/trackers-batch",
                 "/v1/shipping/trackers/{id}", "/v2/checkout/orders/{id}/track"),
    "Reporting": ("/v1/reporting/balances", "/v1/reporting/transactions"),
    "Webhooks": ("/v1/notifications/webhooks", "/v1/notifications/webhooks/{id}",
                 "/v1/notifications/webhooks/{id}/event-types",
                 "/v1/notifications/verify-webhook-signature", "/v1/notifications/simulate-event",
                 "/v1/notifications/webhooks-events", "/v1/notifications/webhooks-events/{id}",
                 "/v1/notifications/webhooks-events/{id}/resend"),
    "OAuth": ("/v1/oauth2/token",),
}
PATTERNS = [(product, route, re.compile("^" + re.escape(route).replace(r"\{id\}", "[^/]+") + "$"))
            for product, routes in ROUTES.items() for route in routes]


def classify(path):
    try:
        path = urlsplit(path).path
    except ValueError:
        return "Other", "/v1/{unmatched}"
    for product, route, pattern in PATTERNS:
        if pattern.fullmatch(path):
            return product, route
    return "Other", "/v2/{unmatched}" if path.startswith("/v2/") else "/v1/{unmatched}"
