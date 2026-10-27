"""Signed inbox deliveries and discovery share a dispute identity and lock."""

import asyncio
from datetime import timedelta

import pytest
from test_harness import world as shared_world

from countersign.db import Repositories
from countersign.fsm.incident import IncidentEvent
from countersign.inbox import Inbox
from countersign.paypal.webhook_verify import WebhookVerifier

world = shared_world
pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def delivery(world):
    await world.engine.seed(world.state)
    registered = await world.http.request(
        "POST", "/v1/notifications/webhooks",
        json={"url": "http://localhost:8000/webhooks/paypal", "event_types": [{"name": "*"}]},
    )
    assert registered.status_code == 201
    webhook_id = registered.body["id"]
    response = await world.controls.post(
        "/_mock/disputes",
        json={"capture_id": world.state["orders"][0]["capture_id"], "amount": "540.00",
              "reason": "MERCHANDISE_OR_SERVICE_NOT_RECEIVED"},
    )
    assert response.status_code == 200
    emitted = world.mock.state.webhook_service
    record = next(r for r in emitted.store.webhooks.events.values()
                  if r["event"]["event_type"] == "CUSTOMER.DISPUTE.CREATED")
    inbox = Inbox(world.database, world.engine._reader, WebhookVerifier(world.http, webhook_id))
    assert await inbox.receive(record["raw"], emitted.headers(record, webhook_id))
    return inbox, record, webhook_id


@pytest.mark.parametrize("order", ["webhook-first", "discovery-first", "concurrent"])
async def test_webhook_and_discovery_leave_one_open_dispute(world, order):
    inbox, first_delivery, webhook_id = await delivery(world)
    lifecycle = world.engine._incidents
    # Import the GET-confirmed dispute without running C24 discovery yet.
    result = await world.engine._reconciler.reconciler.poll_once()
    assert not result.busy and result.disputes.errors == 0
    since = world.settings.clock()
    if order == "concurrent":
        await asyncio.gather(inbox.process_one(), lifecycle.sweep(since=since))
    elif order == "webhook-first":
        assert await inbox.process_one()
        await lifecycle.sweep(since=since)
    else:
        await lifecycle.sweep(since=since)
        assert await inbox.process_one()
    for _ in range(2):
        await lifecycle.sweep(since=since)
        # A distinct delivery of the same resource must be idempotent too.
        event = world.mock.state.webhook_service.emit(
            "CUSTOMER.DISPUTE.UPDATED",
            next(iter(world.mock.state.store.resources.disputes.values())),
        )
        emitted = world.mock.state.webhook_service
        record = emitted.store.webhooks.events[event["id"]]
        assert await inbox.receive(record["raw"], emitted.headers(record, webhook_id))
        assert await inbox.process_one()
    async with world.database.tx() as conn:
        repos = Repositories(conn)
        [dispute] = await repos.disputes.find()
        [incident] = await repos.incidents.find({"kind": "DISPUTE"})
        assert incident["subject"]["dispute_id"] == str(dispute["id"])
        assert incident["state"] == "OPEN" and incident["closed_at"] is None
        event = await repos.webhook_inbox.get(first_delivery["event"]["id"])
        assert (event["signature"], event["status"]) == ("SUCCESS", "PROCESSED")
        assert len(await repos.reservations.find({"state": "ACTIVE"})) == 1
        assert not await repos.ledger_journals.find()
        opened = [a for a in await repos.audit_log.find() if a["kind"] == "INCIDENT_OPENED"]
        assert len(opened) == 1 and opened[0]["details"]["incident_id"] == str(incident["id"])


async def test_discovery_reuses_active_case_even_with_newer_resolved_history(world):
    inbox, record, webhook_id = await delivery(world)
    assert await inbox.process_one()
    lifecycle = world.engine._incidents
    async with world.database.tx() as conn:
        repos = Repositories(conn)
        [incident] = await repos.incidents.find({"kind": "DISPUTE"})
        # Historical data may have a newer closed case. It must never hide the
        # live case or grant permission for a second investigation.
        await repos.incidents.insert(
            kind="DISPUTE", subject=incident["subject"], state="RESOLVED",
            opened_at=world.settings.clock() + timedelta(days=1),
            closed_at=world.settings.clock() + timedelta(days=1),
        )
    await lifecycle.transition(
        incident["id"], IncidentEvent.INVESTIGATION_STARTED,
        expected_state="OPEN", expected_version=incident["version"],
    )
    await lifecycle.transition(
        incident["id"], IncidentEvent.OWNER_NEEDED,
        expected_state="INVESTIGATING", expected_version=incident["version"] + 1,
    )
    await lifecycle.sweep(since=world.settings.clock())
    emitted = world.mock.state.webhook_service
    event = emitted.emit("CUSTOMER.DISPUTE.UPDATED", record["event"]["resource"])
    repeated = emitted.store.webhooks.events[event["id"]]
    assert await inbox.receive(repeated["raw"], emitted.headers(repeated, webhook_id))
    assert await inbox.process_one()
    async with world.database.tx() as conn:
        cases = await Repositories(conn).incidents.find({"kind": "DISPUTE"})
        assert len(cases) == 2
        [active] = [row for row in cases if row["state"] != "RESOLVED"]
        assert active["id"] == incident["id"] and active["state"] == "AWAITING_OWNER"
        assert active["version"] == incident["version"] + 2
