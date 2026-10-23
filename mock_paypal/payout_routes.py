"""HTTP payout routes; money mutations serialize with clock and reset controls."""

import asyncio

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
        record = state.request_keys.get(request_id) if request_id else None
        if record and record.in_progress:
            raise PayoutError(
                service.behavior.concurrent_duplicate_status,
                service.behavior.concurrent_duplicate_name,
                "Request with this ID is still in progress",
            )
        # Completed request IDs win before sender_batch_id or new body validation.
        if record and service.behavior.replay_original_body_wins:
            return JSONResponse(
                status_code=201, content=service.replay_doc(record, base)
            )
        try:
            if (
                request.headers.get("content-type", "").split(";")[0].strip().lower()
                != "application/json"
            ):
                raise ValueError("Use JSON")
            body = CreatePayout.model_validate(await request.json())
        except (ValueError, UnicodeError, ValidationError):
            raise PayoutError(422, "INVALID_REQUEST", "Invalid payout body") from None
        if record:
            if body.model_dump(exclude_none=True) != record.original_body:
                raise PayoutError(400, "INVALID_REQUEST", "Request ID body changed")
            return JSONResponse(
                status_code=201, content=service.replay_doc(record, base)
            )
        sender_id = body.sender_batch_header.sender_batch_id
        previous_id = state.sender_keys.get(sender_id)
        if previous_id:
            raise PayoutError(
                service.behavior.duplicate_batch_status,
                service.behavior.duplicate_batch_name,
                "Batch with given sender_batch_id already exists",
                details=[
                    {
                        "field": "sender_batch_id",
                        "value": sender_id,
                        "location": "body",
                        "issue": service.behavior.duplicate_batch_issue,
                    }
                ],
                links=[
                    service.link(base, f"/v1/payments/payouts/{previous_id}", "self")
                ],
            )
        batch = service.create(body)
        batch.request_id = request_id
        record = service.remember_keys(batch, request_id, body)

    try:
        # Scheduling checkpoint only: zero mock time passes. Allow overlapping requests
        # to observe the committed operation while its response is still in progress.
        await asyncio.sleep(0)
        async with lock:
            if store.payouts is not state:
                raise PayoutError(409, "MOCK_RESET", "Mock was reset during request")
            result = service.create_doc(batch, base)
    finally:
        # Commit already happened: retries must adopt this batch even if delivery fails.
        record.in_progress = False
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
