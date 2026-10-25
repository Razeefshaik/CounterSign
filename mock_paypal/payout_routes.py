"""HTTP payout routes; money mutations serialize with clock and reset controls."""

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from mock_paypal.payout_models import CreatePayout
from mock_paypal.payouts import PayoutError

router = APIRouter()


@router.post("/v1/payments/payouts", status_code=201)
async def create(request: Request):
    service = request.app.state.payout_service
    store = request.app.state.store
    lock = request.app.state.control_lock
    request_id = request.headers.get("paypal-request-id") or None
    base = str(request.base_url)
    async with lock:
        state = store.payouts
        try:
            if (
                request.headers.get("content-type", "").split(";")[0].strip().lower()
                != "application/json"
            ):
                raise ValueError("Use JSON")
            body = CreatePayout.model_validate(await request.json())
        except (ValueError, UnicodeError, ValidationError):
            raise PayoutError(422, "INVALID_REQUEST", "Invalid payout body") from None
        sender_id = body.sender_batch_header.sender_batch_id
        previous_id = state.sender_keys.get(sender_id)
        if previous_id:
            link_base = (
                "https://api.sandbox.paypal.com"
                if store.config.duplicate_link_host == "sandbox"
                else base
            )
            raise PayoutError(
                400,
                "USER_BUSINESS_ERROR",
                "User business error.",
                information_link="https://developer.paypal.com/docs/api/payments.payouts-batch/#errors",
                details=[
                    {
                        "field": "SENDER_BATCH_ID",
                        "location": "body",
                        "issue": "Batch with given sender_batch_id already exists",
                        "link": [
                            {
                                **service.link(
                                    link_base,
                                    f"/v1/payments/payouts/{previous_id}",
                                    "self",
                                ),
                                "encType": "application/json",
                            }
                        ],
                    }
                ],
            )
        batch = service.create(body)
        batch.request_id = request_id
        service.remember_keys(batch)
        result = service.create_doc(batch, base)
    return JSONResponse(status_code=201, content=result)


@router.get("/v1/payments/payouts/{batch_id}")
async def get_batch(
    batch_id: str,
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=1000, ge=1, le=15000),
    total_required: bool = False,
):
    async with request.app.state.control_lock:
        service = request.app.state.payout_service
        return service.batch_doc(
            service.get_batch(batch_id),
            str(request.base_url),
            page,
            page_size,
            total_required,
        )


@router.get("/v1/payments/payouts-item/{item_id}")
async def get_item(item_id: str, request: Request):
    async with request.app.state.control_lock:
        service = request.app.state.payout_service
        return service.item_doc(
            service.get_item(item_id), str(request.base_url), standalone=True
        )


@router.post("/v1/payments/payouts-item/{item_id}/cancel")
async def cancel(item_id: str, request: Request):
    async with request.app.state.control_lock:
        service = request.app.state.payout_service
        return service.item_doc(
            service.cancel(item_id), str(request.base_url), standalone=True
        )
