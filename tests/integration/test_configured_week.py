"""Configured fake decisions run the real week/services, never an LLM provider."""

import json

import pytest
from test_demo_week_mock import EMAILS, PRIOR, demo_world

from countersign.agent.models import ModelError
from scripts import demo_week as demo
from scripts.slice_e2e import SliceError


class WeekModel:
    model_id = "configured-offline-week"

    def __init__(self, *, cancel=True, injection=True, quota=False):
        self.cancel, self.injection, self.quota = cancel, injection, quota
        self.cancelled = False
        self.requests = []

    async def complete(self, request):
        self.requests.append(request)
        if self.quota:
            self.quota = False
            raise ModelError("MODEL_QUOTA_EXHAUSTED")
        incident = request["data"]["incident"]
        context = request["data"]["context"]
        subject = context["incident_subject"]
        reason = subject.get("reason", "")
        dispute = subject.get("paypal_dispute_id")
        action, params, refs = "WAIT", {}, []
        if self.cancel and not self.cancelled:
            action, params = "CANCEL_UNCLAIMED_PAYOUT", {"payout_item_id": PRIOR}
            self.cancelled = True
        elif "DEMO_EVIDENCE" in reason:
            action, params = "PROVIDE_EVIDENCE", {"paypal_dispute_id": dispute}
            refs = ["tracking:9400100000000000000000"]
        elif "DEMO_MAKER_A_NOTE" in reason:
            action, params = "DRAFT_MESSAGE", {"message": "The share remains reserved."}
        elif "DEMO_OWNER_CLAIM" in reason:
            action, params = "ACCEPT_CLAIM", {"paypal_dispute_id": dispute}
        elif "DEMO_INJECTION" in reason and self.injection:
            action, params = "ACCEPT_CLAIM", {"paypal_dispute_id": dispute,
                                                "refund_to": "attacker@example.test"}
        elif "DEMO_SETTLED_RETRY" in reason:
            action, params = "CREATE_REPLACEMENT_PAYOUT", {"line_id": reason.split("_LINE_")[1]}
        return {"incident_id": incident["id"], "action": action, "params": params,
                "evidence_refs": refs, "rationale": "An offline configured decision over observed records.",
                "expected_effect": {"available_change": {"minor": 0, "currency": "USD"}}}


async def play(world, model):
    # Same normal Commander/registry, only its ChatModel implementation is fake.
    world.engine._agent._commander.model = model
    return await demo.run_week(
        world.engine, world.store, http=world.http, control=world.control, owner=world.owner,
        model=model, advance_to=world.advance_to, manual=world.manual, emails=EMAILS,
        options=demo.DemoOptions(poll_limit=15),
    )


def events(world):
    return [json.loads(line) for line in (world.store.directory / "run.jsonl").read_text().splitlines()]


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize("injection", [True, False], ids=["all-moments", "honest-gap"])
async def test_configured_week_allows_monday_cancel_and_finishes_once(
    db_schema, tmp_path, monkeypatch, injection
):
    async with demo_world(db_schema, tmp_path, monkeypatch) as world:
        model = WeekModel(injection=injection)
        report = await play(world, model)
        earlier = [e for e in events(world) if e["event"] == "CONFIGURED_MODEL_ACTED_EARLIER"]
        assert any(e["action"] == "CANCEL_UNCLAIMED_PAYOUT" and e["action_at"].startswith("2026-11-02")
                   for e in earlier)
        assert report["safety_moments"]["injection"]["shown"] is injection
        assert report["safety_moments"]["settled_retry"]["shown"]
        assert report["configured_status"] == ("COMPLETE" if injection else "COMPLETE_WITH_GAPS")
        truth = (await world.control.get("/_mock/ground-truth")).json()
        paid = [p for p in truth["payout_items"] if p["status"] == "SUCCESS"]
        assert len(paid) == 4
        assert sorted(p["amount"]["value"] for p in paid) == ["300.00", "300.00", "360.00", "486.00"]
        assert report["final"]["cash"]["paypal"]["available"]["minor"] == 35400
        assert next(m for m in report["final"]["makers"] if m["display_name"] == "Maker B")["reserved"]["minor"] == 5400
        assert any(e["event"] == "CONFIGURED_MODEL_CASH" for e in events(world))
        before = [(r["method"], r["path"]) for r in truth["requests"] if r["method"] == "POST"]
        await play(world, model)
        truth = (await world.control.get("/_mock/ground-truth")).json()
        assert [(r["method"], r["path"]) for r in truth["requests"] if r["method"] == "POST"] == before


@pytest.mark.integration
@pytest.mark.asyncio
async def test_configured_week_never_forces_missing_cancel(db_schema, tmp_path, monkeypatch):
    async with demo_world(db_schema, tmp_path, monkeypatch) as world:
        with pytest.raises(SliceError, match="^CONFIGURED_MODEL_DECISION_DIFFERS$"):
            await play(world, WeekModel(cancel=False))
        assert any(e["event"] == "CONFIGURED_MODEL_DECISION_DIFFERS" and e["step"] == "cancel"
                   for e in events(world))
        assert any(e["event"] == "CONFIGURED_MODEL_SUMMARY" and e["status"] == "STOPPED"
                   for e in events(world))
        truth = (await world.control.get("/_mock/ground-truth")).json()
        assert len(truth["payout_items"]) == 1 and truth["payout_items"][0]["status"] == "UNCLAIMED"
        assert not any(r["path"].endswith("/cancel") and r["method"] == "POST" for r in truth["requests"])


@pytest.mark.integration
@pytest.mark.asyncio
async def test_quota_stops_fixed_and_resumes_saved_week(db_schema, tmp_path, monkeypatch):
    async with demo_world(db_schema, tmp_path, monkeypatch) as world:
        model = WeekModel(quota=True)
        with pytest.raises(SliceError, match="^MODEL_QUOTA_EXHAUSTED$"):
            await play(world, model)
        assert world.store.state["steps"]["seed"] == "SUCCEEDED"
        assert world.store.state["steps"]["monday_dispute"] == "IN_PROGRESS"
        assert world.store.data["configured_status"] == "MODEL_QUOTA_EXHAUSTED"
        report = await play(world, model)
        assert report["configured_status"] == "COMPLETE"
        assert sum(e["event"] == "MODEL_QUOTA_EXHAUSTED" for e in events(world)) == 1
