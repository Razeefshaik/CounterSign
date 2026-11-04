"""Real week services and decision export; scripted model only, owned PostgreSQL."""

import json

import pytest
from jsonschema import validate
from test_demo_week_mock import EMAILS, demo_world

from scripts import demo_week as demo
from scripts.c67_week_recording import DECISION_SCHEMA


@pytest.mark.asyncio
async def test_real_week_exports_reads_proposals_flags_verdicts_usage_and_redacts(db_schema, tmp_path, monkeypatch):
    async with demo_world(db_schema, tmp_path, monkeypatch) as world:
        world.store.c67_record_decisions = True
        complete = world.model.complete
        planted_key = "sk-or-v1-" + "x" * 40
        planted_email = "planted.person@private-company.invalid"
        async def annotated(request):
            answer = await complete(request)
            world.model.last_usage_metadata = {"input_tokens": 7, "output_tokens": 3}
            if "action" in answer:
                answer["rationale"] += " " + planted_key + " " + planted_email
                answer["flags"] = []
            return answer
        monkeypatch.setattr(world.model, "complete", annotated)
        report = await demo.run_week(world.engine, world.store, http=world.http, control=world.control,
            owner=world.owner, model=world.model, advance_to=world.advance_to, manual=world.manual,
            target="mock", options=demo.DemoOptions(), emails=EMAILS)
        path = world.store.directory / "decisions.jsonl"
        text = path.read_text()
        assert planted_key not in text and planted_email not in text and "[REDACTED]" in text
        rows = [json.loads(line) for line in text.splitlines()]
        assert rows and all(row["step"] != "between_steps" for row in rows)
        for row in rows:
            validate(row, DECISION_SCHEMA)
            assert row["model"]["action"] and isinstance(row["model"]["params"], dict)
            assert row["model"]["rationale"]
            assert row["model"]["injection_flags"] == row["model"]["payee_change_flags"] == []
            assert row["shield"]["verdict"] in {"ALLOW", "BLOCK", "NEEDS_OWNER"}
            assert isinstance(row["shield"]["rules"], list) and isinstance(row["shield"]["flags"], list)
            assert row["usage"]["input_tokens"] == 7 * len(row["turns"])
            assert row["usage"]["output_tokens"] == 3 * len(row["turns"])
            assert row["seconds"] >= 0
        reads = [read for row in rows for read in row["reads"]]
        assert any(row["shield"]["rules"] for row in rows)
        assert {"get_dispute", "get_shipment_tracking", "get_payout_item"} <= {read["tool"] for read in reads}
        assert all(read["arguments"] and read["result_summary"] for read in reads)
        turns = [json.loads(line) for line in (world.store.directory / "turns.jsonl").read_text().splitlines()]
        assert len(turns) == world.store.data["c67_model_turns"] <= 60
        assert all(t["status"] == "passed" and t["usage"]["total_tokens"] == 10 for t in turns)
        # The ordinary event log stays fixed-code only, without any decision payload.
        events = (world.store.directory / "run.jsonl").read_text()
        assert "RECORDED_MODEL_DECISION" not in events and "rationale" not in events
        assert "DEMO_COMPLETE" in events and report["final"]["cash"]["paypal"]["available"]["minor"] == 35400
        money = json.loads((world.store.directory / "money.json").read_text())
        assert money["cash"]["paypal"]["available"]["minor"] == 35400
