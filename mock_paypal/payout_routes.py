"""M02 HTTP routes; mutations serialize with mock clock and reset controls."""

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from mock_paypal.payout_models import CreatePayout

router = APIRouter()


@router.post("/v1/payments/payouts", status_code=201)
async def create(body: CreatePayout, request: Request):
    async with request.app.state.control_lock:
        service = request.app.state.payout_service
        batch = service.create(body)
        return JSONResponse(
            status_code=201, content=service.create_doc(batch, str(request.base_url))
        )


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
