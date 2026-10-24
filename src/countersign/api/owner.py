"""Owner-only decisions, mounted by main at /api/v1."""

from hmac import compare_digest
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from psycopg import Error as DatabaseError
from psycopg_pool import PoolTimeout
from pydantic import BaseModel, ConfigDict, Field

from countersign.approvals import ApprovalError, ApprovalService
from countersign.config import Settings
from countersign.db import Database


class OwnerBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ApproveBody(OwnerBody):
    payload_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    approved_by: str = Field(default="owner", min_length=1, max_length=100)


class RejectBody(OwnerBody):
    reason: str = Field(min_length=1, max_length=2000)


class PayeeBody(OwnerBody):
    payout_email: str = Field(min_length=3, max_length=254)
    confirm: Literal[True]


def owner_guard(settings: Settings):
    async def check(x_owner_token: Annotated[str | None, Header()] = None):
        if settings.mode == "demo" and settings.paypal.is_mock:
            return
        expected = settings.owner_token
        if (
            not expected
            or not x_owner_token
            or not compare_digest(expected.encode(), x_owner_token.encode())
        ):
            raise HTTPException(status_code=401, detail={"code": "OWNER_AUTH_REQUIRED"})

    return check


async def public_result(awaitable):
    try:
        return await awaitable
    except ApprovalError as exc:
        raise HTTPException(status_code=exc.status, detail={"code": exc.code}) from None
    except (DatabaseError, PoolTimeout):
        raise HTTPException(status_code=503, detail={"code": "OWNER_STORAGE_UNAVAILABLE"}) from None


async def owner_document(database: Database, resource: str, row_id: UUID) -> dict:
    """Return the contract document, never private storage columns or raw payloads."""
    queries = {
        "proposal": "SELECT doc FROM v_dash_proposals WHERE id=%s",
        "maker": "SELECT doc FROM v_dash_makers WHERE id=%s",
        "run": "SELECT doc FROM v_dash_runs WHERE id=%s",
    }
    try:
        async with database.tx() as conn:
            cursor = await conn.execute(queries[resource], (row_id,))
            row = await cursor.fetchone()
    except (DatabaseError, PoolTimeout):
        raise HTTPException(status_code=503, detail={"code": "OWNER_STORAGE_UNAVAILABLE"}) from None
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "RESOURCE_NOT_FOUND"})
    return row["doc"]


def build_router(database: Database, settings: Settings) -> APIRouter:
    router = APIRouter(dependencies=[Depends(owner_guard(settings))])
    service = ApprovalService(database)

    @router.post("/proposals/{id}/approve", status_code=201)
    async def approve(id: UUID, body: ApproveBody):
        approval = await public_result(service.approve(id, body.payload_hash, body.approved_by))
        return {"approval": approval}

    @router.post("/proposals/{id}/reject")
    async def reject(id: UUID, body: RejectBody):
        await public_result(service.reject(id, body.reason))
        return await owner_document(database, "proposal", id)

    @router.post("/makers/{id}/payout-email")
    async def payout_email(id: UUID, body: PayeeBody):
        await public_result(service.set_payout_email(id, body.payout_email))
        return await owner_document(database, "maker", id)

    return router
