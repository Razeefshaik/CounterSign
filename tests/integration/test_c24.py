"""C24 rules, simulator and incident lifecycle against PostgreSQL and stubbed PayPal."""

from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
from test_harness import WEEK, ready_poll
from test_harness import world as shared_world

from countersign.approvals.service import ApprovalError, load_context
from countersign.db import Repositories, StaleRow
from countersign.fsm.incident import IncidentEvent
from countersign.incidents import IncidentService
from countersign.paypal.http import canonical_payload_hash
from countersign.shield.engine import Verdict, evaluate

world = shared_world
pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def seeded(world):
    await world.engine.seed(world.state)
    await ready_poll(world.engine)
    return world.settings.clock()


async def snapshot_rows(world):
    async with world.database.tx() as conn:
        repos = Repositories(conn)
        return {
            name: await getattr(repos, name).find()
            for name in (
                "incidents",
                "proposals",
                "operations",
                "reservations",
                "obligations",
                "policies",
                "audit_log",
            )
        }


async def add_dispute(world):
    response = await world.controls.post(
        "/_mock/disputes",
        json={
            "capture_id": world.state["orders"][0]["capture_id"],
            "amount": "540.00",
            "reason": "MERCHANDISE_OR_SERVICE_NOT_RECEIVED",
        },
    )
    assert response.status_code == 200
    await ready_poll(world.engine)
    async with world.database.tx() as conn:
        return (await Repositories(conn).disputes.find())[0]


async def policies(conn, amount=100000):
    repos = Repositories(conn)
    for key, value in {
        "deadline_close_seconds": 3600,
        "refund_limit_per_order": {"minor": amount, "currency": "USD"},
        "refund_limit_per_day": {"minor": amount, "currency": "USD"},
    }.items():
        previous = await repos.policies.get(key)
        if previous:
            await repos.policies.update(key, value=value)
        else:
            await repos.policies.insert(key=key, value=value)


async def test_rule_fact_loader_preserves_hash_and_blocks_missing_policy(world):
    now = await seeded(world)
    dispute = await add_dispute(world)
    async with world.database.tx() as conn:
        repos = Repositories(conn)
        params = {"paypal_dispute_id": dispute["paypal_dispute_id"]}
        row = await repos.proposals.insert(
            action="ACCEPT_CLAIM",
            params=params,
            evidence_refs=[],
            model="test",
            raw_output={},
            state="CREATED",
        )
        proposal, snap, payload = await load_context(conn, row, now=now)
        assert payload["action"] == "ACCEPT_CLAIM" and payload["params"] == params
        assert payload["request"]["target_id"] == str(dispute["id"])
        assert payload["request"]["refund_amount"] == {"minor": 54000, "currency": "USD"}
        assert proposal.payload_hash == canonical_payload_hash(payload)
        assert snap.disputes[0].seller_response_due_date == dispute["seller_response_due_date"]
        assert (
            evaluate(proposal, snap).verdict is Verdict.BLOCK
        )  # scenario limit is below this claim
        await policies(conn)
        proposal, snap, _ = await load_context(conn, row, now=now)
        assert evaluate(proposal, snap).verdict is Verdict.NEEDS_OWNER
        await repos.policies.delete("refund_limit_per_order")
        proposal, snap, _ = await load_context(conn, row, now=now)
        assert evaluate(proposal, snap).verdict is Verdict.BLOCK
        await policies(conn)
        # An old ambiguous claim on an unlinked dispute may still spend today's
        # refund budget. Its observed refund must not be counted a second time.
        other = await repos.disputes.insert(
            paypal_dispute_id="PP-D-UNLINKED",
            status="RESOLVED",
            phase="UNKNOWN_OUTCOME",
            amount_minor=50000,
            amount_refunded_minor=50000,
            currency="USD",
            raw={"update_time": "2026-10-01T00:00:00Z"},
        )
        await repos.operations.insert(
            kind="DISPUTE_ACCEPT_CLAIM",
            target_type="dispute",
            target_id=other["id"],
            payload={},
            payload_hash=bytes(32),
            state="UNKNOWN",
            created_at=now - timedelta(days=2),
            submitted_at=now - timedelta(days=2),
        )
        proposal, snap, _ = await load_context(conn, row, now=now)
        assert snap.refund_totals["day"]["USD"] == 50000
        assert evaluate(proposal, snap).verdict is Verdict.BLOCK


async def test_normal_dispute_discovered_once_and_cannot_close_on_unconfirmed_state(world):
    now = await seeded(world)
    dispute = await add_dispute(world)
    rows = await snapshot_rows(world)
    incidents = [r for r in rows["incidents"] if r["kind"] == "DISPUTE"]
    assert len(incidents) == 1
    service = world.engine._incidents
    with pytest.raises(ValueError, match="RECONCILIATION_REQUIRED"):
        await service.transition(
            str(incidents[0]["id"]),
            IncidentEvent.RECONCILED,
            expected_state="OPEN",
            expected_version=1,
        )
    await service.sweep(since=now)
    rows = await snapshot_rows(world)
    assert len([r for r in rows["incidents"] if r["kind"] == "DISPUTE"]) == 1
    assert all(r["closed_at"] is None for r in rows["incidents"])
    assert any(r["kind"] == "RECONCILE_MISMATCH" for r in rows["audit_log"])
    opened = [r for r in rows["audit_log"] if r["kind"] == "INCIDENT_OPENED"]
    assert len(opened) == 1
    assert incidents[0]["subject"]["dispute_id"] == str(dispute["id"])


async def test_get_confirmed_dispute_closes_only_after_financial_resolution(world):
    await seeded(world)
    dispute = await add_dispute(world)
    # Only the test uses the mock's synthetic outcome mapping; production stays pending D10.
    reconciler = world.engine._reconciler.reconciler
    from countersign.fsm.dispute import Phase

    reconciler.settings = replace(
        reconciler.settings, outcome_map={"CANCELED_BY_BUYER": Phase.CANCELLED}
    )
    response = await world.controls.post(
        f"/_mock/disputes/{dispute['paypal_dispute_id']}/buyer-close", json={}
    )
    assert response.status_code == 200
    # A write response alone has not changed CounterSign or closed its incident.
    assert all(r["closed_at"] is None for r in (await snapshot_rows(world))["incidents"])
    await ready_poll(world.engine)
    rows = await snapshot_rows(world)
    [incident] = [r for r in rows["incidents"] if r["kind"] == "DISPUTE"]
    assert incident["state"] == "RESOLVED" and incident["closed_at"] is not None
    assert rows["obligations"][0]["state"] == "OPEN"
    assert all(r["state"] != "ACTIVE" for r in rows["reservations"])
    assert len([r for r in rows["audit_log"] if r["kind"] == "INCIDENT_RESOLVED"]) == 1
    await ready_poll(world.engine)
    assert (
        len(
            [
                r
                for r in (await snapshot_rows(world))["audit_log"]
                if r["kind"] == "INCIDENT_RESOLVED"
            ]
        )
        == 1
    )

    async with world.database.tx() as conn:
        repos = Repositories(conn)
        delayed = await repos.incidents.insert(
            kind="RECONCILIATION",
            subject={"entity": "dispute", "id": str(dispute["id"]), "reason": "DELAYED_REVIEW"},
            state="INVESTIGATING",
        )
        operation = await repos.operations.insert(
            kind="DISPUTE_EVIDENCE",
            target_type="dispute",
            target_id=dispute["id"],
            payload={},
            payload_hash=bytes(32),
            state="UNKNOWN",
        )
    await world.engine._incidents.sweep(since=world.settings.clock() + timedelta(seconds=1))
    async with world.database.tx() as conn:
        repos = Repositories(conn)
        assert (await repos.incidents.get(delayed["id"]))["state"] == "INVESTIGATING"
        # The simulated write was proved not sent; the old terminal GET remains valid.
        await repos.operations.update(operation["id"], state="FAILED")
    await world.engine._incidents.sweep(since=world.settings.clock() + timedelta(seconds=1))
    async with world.database.tx() as conn:
        assert (await Repositories(conn).incidents.get(delayed["id"]))["state"] == "RESOLVED"
    # Fault injection only: the mock control refuses transitions from RESOLVED.
    # A contradictory later observation still must reopen investigation safely.
    external = world.mock.state.store.resources.disputes[dispute["paypal_dispute_id"]]
    external.update(status="UNDER_REVIEW", dispute_outcome=None)
    changed = await world.controls.post(
        f"/_mock/disputes/{dispute['paypal_dispute_id']}/transition",
        json={"status": "WAITING_FOR_SELLER_RESPONSE", "stage": "CHARGEBACK"},
    )
    assert changed.status_code == 200
    # A terminal dispute is no longer in C15's scheduled polling set. Model the
    # inbox's GET-confirmed ingress, rather than assuming the poller re-fetches it.
    from countersign.ledger.journal import serialize_on
    from countersign.reconcile.disputes import ingest_dispute

    response = await world.engine._reader.get_dispute(dispute["paypal_dispute_id"])
    async with world.database.tx() as conn:
        await serialize_on(conn, "countersign:owner-decisions")
        await ingest_dispute(
            conn,
            response.body,
            now=world.settings.clock(),
            outcome_map=reconciler.settings.outcome_map,
        )
    await ready_poll(world.engine)
    cases = [r for r in (await snapshot_rows(world))["incidents"] if r["kind"] == "DISPUTE"]
    assert sorted(r["state"] for r in cases) == ["OPEN", "RESOLVED"]
    await ready_poll(world.engine)
    assert (
        len([r for r in (await snapshot_rows(world))["incidents"] if r["kind"] == "DISPUTE"]) == 2
    )


async def test_executor_awaiting_owner_subject_is_preserved(world):
    now = await seeded(world)
    subject = {
        "operation_id": str(uuid4()),
        "attempt_id": str(uuid4()),
        "reason": "UNKNOWN_PAST_REPLAY_WINDOW",
        "age_days": 26,
    }
    async with world.database.tx() as conn:
        incident = await Repositories(conn).incidents.insert(
            kind="PAYOUT_ITEM", subject=subject, state="AWAITING_OWNER"
        )
    await world.engine._incidents.sweep(since=now)
    async with world.database.tx() as conn:
        saved = await Repositories(conn).incidents.get(incident["id"])
        assert saved["state"] == "AWAITING_OWNER" and saved["subject"] == subject
        assert saved["closed_at"] is None


async def test_incident_cas_rejects_stale_version(world):
    now = await seeded(world)
    service = IncidentService(world.database, clock=lambda: now)
    incident = await service.open(
        "RECONCILIATION", {"entity": "poller", "id": "test", "reason": "STAGE_FAILED"}
    )
    await service.transition(
        str(incident["id"]),
        IncidentEvent.INVESTIGATION_STARTED,
        expected_state="OPEN",
        expected_version=1,
    )
    with pytest.raises(StaleRow):
        await service.transition(
            str(incident["id"]),
            IncidentEvent.INVESTIGATION_STARTED,
            expected_state="OPEN",
            expected_version=1,
        )


async def test_run_at_risk_opens_on_shortfall(world):
    now = await seeded(world)
    await world.engine.plan_payout_run(WEEK)
    async with world.database.tx() as conn:
        await Repositories(conn).balance_snapshots.insert(
            fetched_at=now + timedelta(seconds=1),
            last_refresh_time=now,
            currency="USD",
            available_minor=1,
            raw={},
        )
    await world.engine._incidents.sweep(since=now)
    assert (
        len([r for r in (await snapshot_rows(world))["incidents"] if r["kind"] == "RUN_AT_RISK"])
        == 1
    )


async def test_payout_resolution_requires_matching_get_ledger_and_obligations(world):
    now = await seeded(world)
    await world.engine.plan_payout_run(WEEK)
    [pending] = await world.engine.pending_approvals()
    await world.engine.approve(pending["id"], pending["approval_request"]["payload_hash"])
    await ready_poll(world.engine)
    await world.controls.post("/_mock/clock/advance", json={"seconds": 60})
    await ready_poll(world.engine)
    async with world.database.tx() as conn:
        repos = Repositories(conn)
        [attempt] = await repos.payout_attempts.find()
        [obligation] = await repos.obligations.find()
        assert attempt["state"] == "SUCCESS" and obligation["state"] == "SETTLED"
        # Fault injection corrupts the local record after a successful external payout.
        await repos.obligations.update(obligation["id"], state="IN_FLIGHT")
    service = world.engine._incidents
    incident = await service.open("PAYOUT_ITEM", {"attempt_id": str(attempt["id"])})
    await service.sweep(since=now)
    async with world.database.tx() as conn:
        repos = Repositories(conn)
        saved = await repos.incidents.get(incident["id"])
        assert saved["state"] != "RESOLVED" and saved["closed_at"] is None
        await repos.obligations.update(obligation["id"], state="SETTLED")
    await service.sweep(since=world.settings.clock() + timedelta(seconds=1))
    async with world.database.tx() as conn:
        assert (await Repositories(conn).incidents.get(incident["id"]))["state"] != "RESOLVED"
    await ready_poll(world.engine)
    async with world.database.tx() as conn:
        assert (await Repositories(conn).incidents.get(incident["id"]))["state"] == "RESOLVED"


async def test_commander_evidence_verdict_uses_stored_records_and_action_link(world):
    from test_agent_harness import agent_engine
    from test_agent_harness import proposal as model_proposal

    from countersign.agent.models import ScriptedModel

    await seeded(world)
    dispute = await add_dispute(world)
    changed = await world.controls.post(
        f"/_mock/disputes/{dispute['paypal_dispute_id']}/transition",
        json={"status": "WAITING_FOR_SELLER_RESPONSE", "stage": "CHARGEBACK"},
    )
    assert changed.status_code == 200
    await ready_poll(world.engine)
    async with world.database.tx() as conn:
        repos = Repositories(conn)
        await policies(conn)
        order = await repos.orders.get(dispute["order_id"])
        await repos.orders.update(
            order["id"],
            tracking={
                "carrier": "USPS",
                "tracking_number": "TRACK-STORED",
                "tracking_number_validated": False,
            },
        )
        [incident] = await repos.incidents.find({"kind": "DISPUTE"})
    document = model_proposal(
        incident["id"], "PROVIDE_EVIDENCE", {"paypal_dispute_id": dispute["paypal_dispute_id"]}
    )
    document["evidence_refs"] = [f"order:{order['id']}", "tracking:TRACK-STORED"]
    async with agent_engine(world, ScriptedModel([document])) as engine:
        assert (await engine.tick())["errors"] == 0
    rows = await snapshot_rows(world)
    [saved] = rows["proposals"]
    assert saved["verdict"] == "ALLOW"
    assert world.engine._simulator.database is world.database
    results = saved["verdict_details"]["rule_results"]
    assert next(r for r in results if r["rule"] == "EVIDENCE_INTEGRITY")["outcome"] == "PASS"
    [operation] = rows["operations"]
    assert saved["state"] == "EXECUTED"
    assert operation["kind"] == "DISPUTE_EVIDENCE" and operation["state"] == "SUCCEEDED"
    assert operation["payload"]["body"]["evidences"][0]["evidence_info"]["tracking_info"] == [
        {"carrier_name": "USPS", "tracking_number": "TRACK-STORED"}
    ]
    truth = (await world.controls.get("/_mock/ground-truth")).json()
    assert (
        sum(
            r["method"] == "POST" and r["path"].endswith("/provide-evidence")
            for r in truth["requests"]
        )
        == 1
    )
    async with world.database.tx() as conn:
        repos = Repositories(conn)
        current = await repos.disputes.get(dispute["id"])
        await repos.disputes.update(current["id"], links=[])
        with pytest.raises(ApprovalError, match="ACTION_PRECONDITION_FAILED"):
            await load_context(conn, saved, now=world.settings.clock())


@pytest.mark.parametrize("threshold", [None, 3600])
async def test_core_seed_deadline_default_and_owner_override_survive_reseed(world, threshold):
    from copy import deepcopy

    state = deepcopy(world.state)
    if threshold is None:
        state["policy"].pop("deadline_close_seconds", None)
    else:
        state["policy"]["deadline_close_seconds"] = threshold
    await world.engine.seed(state)
    async with world.database.tx() as conn:
        repos = Repositories(conn)
        assert (await repos.policies.get("deadline_close_seconds"))["value"] == (
            172800 if threshold is None else threshold
        )
        await repos.policies.update("deadline_close_seconds", value=7200)
    await world.engine.seed(state)
    async with world.database.tx() as conn:
        assert (await Repositories(conn).policies.get("deadline_close_seconds"))["value"] == 7200
