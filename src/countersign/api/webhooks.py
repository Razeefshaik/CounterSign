"""Acknowledgement means durable ingress only; verification is a worker duty."""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from countersign.inbox import InboxError


def build_router(inbox):
    router = APIRouter()

    @router.post("/webhooks/paypal")
    async def paypal_webhook(request: Request):
        try:
            await inbox.receive(await request.body(), request.headers)
        except InboxError:
            return JSONResponse(
                {"error": {"code": "INVALID_WEBHOOK", "message": "Invalid webhook event"}},
                status_code=400,
            )
        except Exception:  # noqa: BLE001 - HTTP boundary must hide private driver failures.
            return JSONResponse(
                {"error": {"code": "INBOX_UNAVAILABLE", "message": "Webhook storage unavailable"}},
                status_code=503,
            )
        return {"received": True}

    return router
