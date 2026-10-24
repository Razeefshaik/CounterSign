"""The worker resumes a previously deferred evidence proposal without rewriting it."""

import pytest
import test_actions as actions_tests
from test_actions import endpoint, proposed, target
from test_executor import rows

from countersign import worker
from countersign.db import Repositories
from countersign.shield.snapshot import PAYING

world = actions_tests.world
pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_previously_deferred_evidence_runs_unchanged(world, monkeypatch):
    record = await target(world)
    proposal_id, payload = await proposed(world, "PROVIDE_EVIDENCE", record)
    # C23 stored only action/params for these autonomous proposals. No owner
    # permission is being upgraded: the executor builds a fresh trusted request.
    async with world[0].tx() as conn:
        await Repositories(conn).proposals.update(
            proposal_id,
            raw_output={},
            verdict_details={
                "operation_payload": {"action": payload["action"], "params": payload["params"]}
            },
        )
    async with endpoint(world) as (executor, control):
        with monkeypatch.context() as previous_capabilities:
            previous_capabilities.setattr(worker, "EXECUTABLE", PAYING)
            deferred = await worker.execute_pending(world[0], executor)
        assert deferred == {"proposals": 0, "operations": 0, "errors": 0}
        assert (await rows(world[0], "proposals"))[0]["state"] == "ALLOWED"
        assert await rows(world[0], "approvals") == []
        routed = await worker.execute_pending(world[0], executor)
        assert routed == {"proposals": 1, "operations": 1, "errors": 0}
        assert (await executor.status(proposal_id))["proposal_state"] == "EXECUTED"
        assert len(control["posts"]) == 1
        audits = [
            r
            for r in await rows(world[0], "audit_log")
            if (r["details"] or {}).get("reason") == "EXECUTION_DEFERRED"
        ]
        assert len(audits) == 1
