"""Only actual, bounded read metadata survives into a proposal trace."""

import asyncio
import json

import pytest

from countersign.agent.commander import Commander
from countersign.agent.models import ScriptedModel
from countersign.agent.tools import ToolError
from countersign.api.dashboard import public_trace


class Reads:
    schemas = ()

    def __init__(self, fail=False):
        self.fail = fail
        self.calls = 0

    async def invoke(self, name, arguments):
        self.calls += 1
        if self.fail:
            raise ToolError("TOOLKIT_CALL_FAILED")
        return {"content": "OUTPUT_TEXT_MUST_NOT_BE_SAVED", "private": "fake-secret"}


def proposal():
    return {"incident_id": "incident-1", "action": "WAIT", "params": {}, "evidence_refs": [],
            "rationale": "Wait for confirmation.",
            "expected_effect": {"available_change": {"minor": 0, "currency": "USD"}}}


@pytest.mark.parametrize("fail", [False, True])
def test_trace_masks_email_discards_free_text_and_records_fixed_failure(fail):
    async def run():
        tools = Reads(fail)
        model = ScriptedModel([{"tool_calls": [{"id": "read-1", "name": "get_dispute",
            "args": {"dispute_id": "PP-D-1", "order_id": "private.person@example.test",
                     "note": "PRIVATE_TEXT", "client_secret": "fake-secret"}}]}, proposal()])
        return await Commander(model, tools, secrets=("fake-secret",)).step({"id": "incident-1"})

    saved = asyncio.run(run()).raw_output["agent_trace"]
    assert saved["rounds"] == 2
    assert saved["calls"] == [{"tool": "get_dispute",
        "arguments": {"dispute_id": "PP-D-1", "order_id": "p***@example.test"},
        "ok": not fail, "error_code": "TOOLKIT_CALL_FAILED" if fail else None}]
    rendered = json.dumps(saved)
    for private in ("private.person", "PRIVATE_TEXT", "OUTPUT_TEXT", "fake-secret", "client_secret"):
        assert private not in rendered
    assert public_trace(saved) == saved


def test_trace_is_capped_at_sixteen_calls_and_does_not_carry_into_next_step():
    async def run():
        tools = Reads()
        turns = [{"tool_calls": [{"id": f"read-{batch}-{n}", "name": "get_dispute",
                  "args": {"dispute_id": "PP-D-1"}} for n in range(4)]} for batch in range(5)]
        commander = Commander(ScriptedModel([*turns, proposal(), proposal()]), tools,
                              max_tool_calls=32, max_rounds=8)
        first = await commander.step({"id": "incident-1"})
        second = await commander.step({"id": "incident-1"})
        return tools, first.raw_output["agent_trace"], second.raw_output["agent_trace"]

    tools, first, second = asyncio.run(run())
    assert tools.calls == 20 and len(first["calls"]) == 16 and first["rounds"] == 6
    assert second["calls"] == [] and second["rounds"] == 1
    assert public_trace(None) is None
