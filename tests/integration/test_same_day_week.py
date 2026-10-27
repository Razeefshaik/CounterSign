"""Same-step and saved-stop races use real services and only fake models."""

from uuid import UUID

import pytest
from test_configured_week import WeekModel, events, play
from test_demo_week_mock import PRIOR, demo_world

from countersign.db import Repositories
from countersign.fsm.incident import IncidentEvent
from countersign.incidents import IncidentService
from scripts.slice_e2e import SliceError


class SameDayModel(WeekModel):
    def __init__(self, action):
        super().__init__(cancel=False)
        self.action = action
        self.armed = False

    async def complete(self, request):
        answer = await super().complete(request)
        subject = request["data"]["context"]["incident_subject"]
        reason = subject.get("reason", "")
        if self.armed and (self.action == "CANCEL_UNCLAIMED_PAYOUT"
                           or subject.get("paypal_dispute_id")):
            answer["action"] = self.action
            answer["params"] = (
                {"payout_item_id": PRIOR} if self.action == "CANCEL_UNCLAIMED_PAYOUT"
                else {"paypal_dispute_id": subject["paypal_dispute_id"]}
            )
            answer["evidence_refs"] = (
                [] if self.action == "CANCEL_UNCLAIMED_PAYOUT"
                else ["tracking:9400100000000000000000"]
            )
        elif "DEMO_CANCEL" in reason:
            # Other evidence-race steps may use the ordinary staged cancellation.
            answer["action"] = "CANCEL_UNCLAIMED_PAYOUT"
            answer["params"] = {"payout_item_id": PRIOR}
        return answer


async def install_race(world, monkeypatch, action, *, saved_stop=False):
    label = "cancel" if action == "CANCEL_UNCLAIMED_PAYOUT" else "evidence"
    kind = "PAYOUT_ITEM" if label == "cancel" else "DISPUTE"
    model = SameDayModel(action)
    original_open, original_tick = IncidentService.open, world.engine.tick
    race = {"case": None, "ran": False}

    async def open_goal(service, goal_kind, subject):
        case = await original_open(service, goal_kind, subject)
        if subject.get("reason", "").startswith("DEMO_" + label.upper()):
            race["case"] = str(case["id"])
            async with world.database.tx() as conn:
                rows = await Repositories(conn).incidents.find({"kind": kind})
            original = next(r for r in rows if r["id"] != case["id"])
            if original["state"] == "WAITING_EXTERNAL":
                await service.transition(original["id"], IncidentEvent.EXTERNAL_UPDATE,
                                         expected_state=original["state"],
                                         expected_version=original["version"])
        return case

    async def raced_tick():
        if race["case"] and not race["ran"]:
            race["ran"] = True
            model.armed = True
            # Reproduce an already-stopped rehearsal: ordinary ticks execute the
            # original action, then the real shield refuses its staged duplicate.
            for _ in range(20):
                result = await original_tick()
                assert result["errors"] == 0
                snapshot = await world.engine.snapshot()
                duplicate = [p for p in snapshot["proposals"]
                             if p["incident_id"] == race["case"] and p["state"] == "BLOCKED"]
                if duplicate:
                    break
            else:
                pytest.fail("the staged duplicate was not shield-blocked")
            model.armed = False
            if saved_stop:
                world.store.record("CONFIGURED_MODEL_DECISION_DIFFERS", step=label,
                                   expected_action=action, action=action)
                raise SliceError("CONFIGURED_MODEL_DECISION_DIFFERS")
            return result
        return await original_tick()

    monkeypatch.setattr(IncidentService, "open", open_goal)
    monkeypatch.setattr(world.engine, "tick", raced_tick)
    return model, race


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["CANCEL_UNCLAIMED_PAYOUT", "PROVIDE_EVIDENCE"])
@pytest.mark.parametrize("saved_stop", [False, True], ids=["same-step", "resume"])
async def test_original_action_and_blocked_staged_duplicate_continue_once(
    db_schema, tmp_path, monkeypatch, action, saved_stop
):
    async with demo_world(db_schema, tmp_path, monkeypatch) as world:
        model, race = await install_race(world, monkeypatch, action, saved_stop=saved_stop)
        if saved_stop:
            with pytest.raises(SliceError, match="^CONFIGURED_MODEL_DECISION_DIFFERS$"):
                await play(world, model)
        report = await play(world, model)
        assert report["configured_status"] == "COMPLETE"
        label = "cancel" if action == "CANCEL_UNCLAIMED_PAYOUT" else "evidence"
        recorded = events(world)
        acted = [e for e in recorded if e["event"] == "CONFIGURED_MODEL_ACTED_EARLIER"
                 and e["step"] == label]
        duplicate = [e for e in recorded if e["event"] == "CONFIGURED_MODEL_DUPLICATE_BLOCKED"
                     and e["step"] == label]
        assert len(acted) == len(duplicate) == 1
        assert acted[0]["same_step"] is (not saved_stop)
        assert acted[0]["action_at"]
        assert ("PAYOUT_STATE_MACHINE" if label == "cancel" else "EVIDENCE_INTEGRITY") in duplicate[0]["rules"]
        assert sum(e["event"] == "CONFIGURED_MODEL_DECISION_DIFFERS"
                   and e["step"] == label for e in recorded) == int(saved_stop)
        async with world.database.tx() as conn:
            repos = Repositories(conn)
            staged = await repos.incidents.get(UUID(race["case"]))
            assert staged["state"] == "WAITING_EXTERNAL"
            operations = await repos.operations.find({"kind": "PAYOUT_CANCEL"})
            assert len(operations) == 1 and operations[0]["state"] == "SUCCEEDED"
        calls = len(model.requests)
        await world.engine.tick()
        assert all(r["data"]["incident"]["id"] != race["case"] for r in model.requests[calls:])
        truth = (await world.control.get("/_mock/ground-truth")).json()
        assert sum(r["method"] == "POST" and r["path"].endswith("/cancel")
                   for r in truth["requests"]) == 1
        await play(world, model)
        assert len([e for e in events(world) if e["event"] == "CONFIGURED_MODEL_ACTED_EARLIER"
                    and e["step"] == label]) == 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_blocked_cancel_without_executed_match_still_stops(
    db_schema, tmp_path, monkeypatch
):
    async with demo_world(db_schema, tmp_path, monkeypatch) as world:
        original_open = IncidentService.open

        async def claim_before_goal(service, kind, subject):
            if subject.get("reason", "").startswith("DEMO_CANCEL"):
                response = await world.control.post(f"/_mock/payout-items/{PRIOR}/claim", json={})
                assert response.status_code == 200
            return await original_open(service, kind, subject)

        monkeypatch.setattr(IncidentService, "open", claim_before_goal)
        with pytest.raises(SliceError, match="^CONFIGURED_MODEL_DECISION_DIFFERS$"):
            await play(world, SameDayModel("CANCEL_UNCLAIMED_PAYOUT"))
        recorded = events(world)
        assert any(e["event"] == "CONFIGURED_MODEL_DECISION_DIFFERS" and e["step"] == "cancel"
                   for e in recorded)
        assert not any(e["event"] == "CONFIGURED_MODEL_DUPLICATE_BLOCKED" for e in recorded)
        assert not any(e["event"] == "CONFIGURED_MODEL_ACTED_EARLIER" and e["step"] == "cancel"
                       for e in recorded)
        async with world.database.tx() as conn:
            assert not await Repositories(conn).operations.find({"kind": "PAYOUT_CANCEL"})
