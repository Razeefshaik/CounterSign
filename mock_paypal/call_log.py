"""C65: bounded, body-free public call evidence, outside the fault middleware."""

import hashlib
import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from mock_paypal.call_routes import classify
from mock_paypal.clock import timestamp

SOURCE = "Simulated: PayPal mock"
LIMIT = 64 * 1024
ERRORS = {"USER_BUSINESS_ERROR", "DUPLICATE_SENDER_BATCH_ID", "PREVIOUS_REQUEST_IN_PROGRESS"}


def short_hash(value):
    return hashlib.sha256(value.encode() if isinstance(value, str) else value).hexdigest()[:12] if value else None


def body_json(raw):
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except (ValueError, UnicodeError):
        return {}


def append(store, *, method, route, status, direction="App to PayPal", request_id=None,
           sender_batch_id=None, error=None, original_batch=False, incomplete=False):
    product, route = ("Webhooks", "/webhooks/paypal") if direction == "PayPal to app" else classify(route)
    store.call_sequence += 1
    store.call_counts[product] = store.call_counts.get(product, 0) + 1
    store.calls.append({
        "id":store.call_sequence, "at":timestamp(store.clock.now), "source":SOURCE,
        "method":method if method in {"GET", "POST", "PATCH", "PUT", "DELETE", "HEAD", "OPTIONS"} else "OTHER",
        "route":route, "status":status if type(status) is int and 100 <= status <= 599 else None,
        "product":product, "direction":direction, "request_id_hash":short_hash(request_id),
        "sender_batch_id_hash":short_hash(sender_batch_id),
        "error":error if error in ERRORS else None, "original_batch":bool(original_batch),
        "incomplete":bool(incomplete),
    })


def document(store):
    return {"source":SOURCE, "limit":500, "total":store.call_sequence,
            "counts":dict(store.call_counts), "rows":list(reversed(store.calls))}


class CallLogMiddleware:
    def __init__(self, app, store):
        self.app, self.store = app, store

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith(("/v1/", "/v2/")):
            return await self.app(scope, receive, send)
        # Only the payout POST needs its sender key extracted; no body is retained.
        payout = scope["method"] == "POST" and scope["path"] == "/v1/payments/payouts"
        request_body, response_body = bytearray(), bytearray()
        status, complete = None, False
        async def receiving():
            message = await receive()
            if payout and message["type"] == "http.request" and len(request_body) <= LIMIT:
                request_body.extend(message.get("body", b"")[:LIMIT + 1 - len(request_body)])
            return message
        async def sending(message):
            nonlocal status, complete
            if message["type"] == "http.response.start":
                status = message["status"]
            elif message["type"] == "http.response.body":
                if payout and len(response_body) <= LIMIT:
                    response_body.extend(message.get("body", b"")[:LIMIT + 1 - len(response_body)])
                complete = not message.get("more_body", False)
            await send(message)
        try:
            return await self.app(scope, receiving, sending)
        finally:
            request_doc = body_json(request_body) if payout and len(request_body) <= LIMIT else {}
            response_doc = body_json(response_body) if payout and len(response_body) <= LIMIT else {}
            header = request_doc.get("sender_batch_header")
            sender = header.get("sender_batch_id") if isinstance(header, dict) else None
            details = response_doc.get("details", [])
            links = []
            for detail in details if isinstance(details, list) else []:
                link = detail.get("link") if isinstance(detail, dict) else None
                if isinstance(link, str):
                    links.append(link)
                elif isinstance(link, list):
                    links.extend(item["href"] for item in link if isinstance(item, dict) and isinstance(item.get("href"), str))
            original = any(classify(link) == ("Payouts", "/v1/payments/payouts/{id}") for link in links)
            append(self.store, method=scope["method"], route=scope["path"], status=status,
                   request_id=dict(scope["headers"]).get(b"paypal-request-id"),
                   sender_batch_id=sender if isinstance(sender, str) else None,
                   error=response_doc.get("name"), original_batch=original, incomplete=not complete)


def router():
    result = APIRouter()
    @result.get("/_mock/paypal-calls")
    async def calls(request: Request):
        if request.client.host not in {"127.0.0.1", "::1", "localhost"}:
            return JSONResponse({"error":"LOCAL_ONLY"}, status_code=404)
        if request.url.query:
            return JSONResponse({"error":"INVALID_REQUEST"}, status_code=422)
        return document(request.app.state.store)
    return result
