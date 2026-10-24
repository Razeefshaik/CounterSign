"""Owner run planning. GET run views remain the dashboard's responsibility."""

from datetime import date

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from countersign.api.owner import owner_document, owner_guard, public_result
from countersign.approvals import ApprovalService
from countersign.config import Settings
from countersign.db import Database


class PlanRunBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    week_start: date


def build_router(database: Database, settings: Settings) -> APIRouter:
    router = APIRouter(dependencies=[Depends(owner_guard(settings))])
    service = ApprovalService(database)

    @router.post("/payout-runs", status_code=201)
    async def plan_run(body: PlanRunBody):
        run = await public_result(service.plan_run(body.week_start))
        return {
            **await owner_document(database, "run", run["id"]),
            "proposal_id": run["proposal_id"],
        }

    return router
