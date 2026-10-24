"""The worker resumes a previously deferred evidence proposal without rewriting it."""

import pytest
import test_actions as actions_tests
from test_actions import endpoint, proposed, target
from test_executor import rows

from countersign import worker
from countersign.shield.snapshot import PAYING

world = actions_tests.world
pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_previously_deferred_evidence_runs_unchanged(world, monkeypatch):
    record = await target(world)
    proposal_id, _ = await proposed(world, "PROVIDE_EVIDENCE", record)
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
