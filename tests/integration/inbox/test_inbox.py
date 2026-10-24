"""Durable ingress, authenticity gates and atomic dispute holds on PostgreSQL16."""

import asyncio
import importlib.util
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from countersign.api.webhooks import build_router
from countersign.db import Database, Repositories
from countersign.inbox import Inbox
from countersign.paypal.webhook_verify import Verification

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location(
    "seed_marketplace", ROOT / "scripts/seed_marketplace.py"
)
seed_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(seed_module)


def scenario():
    return json.loads((ROOT / "eval/scenarios/demo_week.json").read_text(encoding="utf-8"))


def raw_event(event_id="WH-1", event_type="CUSTOMER.DISPUTE.CREATED"):
    return json.dumps(
        {"id": event_id, "event_type": event_type, "resource": {"dispute_id": "PP-D-1"}}, indent=3
    ).encode()


class Verifier:
    def __init__(self, *results):
        self.results = list(results or [Verification.SUCCESS])
        self.calls = []

    async def verify(self, raw, headers):
        self.calls.append((raw, headers))
        return self.results.pop(0) if len(self.results) > 1 else self.results[0]


class Reader:
    def __init__(self, database=None):
        self.database = database
        self.calls = []
        self.status_code = 200
        self.detail = {
            "dispute_id": "PP-D-1",
            "status": "WAITING_FOR_SELLER_RESPONSE",
            "dispute_life_cycle_stage": "INQUIRY",
            "reason": "OTHER",
            "dispute_amount": {"currency_code": "USD", "value": "400.00"},
            "disputed_transactions": [{"seller_transaction_id": "MOCKCAP400"}],
            "links": [],
        }

    async def get_dispute(self, resource_id):
        self.calls.append(resource_id)
        if self.database:
            async with self.database.tx() as conn:
                assert not await Repositories(conn).disputes.find()
                assert not await Repositories(conn).reservations.find()
                obligations = await Repositories(conn).obligations.find()
                assert all(row["state"] in {"OPEN", "SCHEDULED"} for row in obligations)
        return SimpleNamespace(status_code=self.status_code, body=self.detail)


async def stored(database, event_id="WH-1"):
    async with database.tx() as conn:
        return await Repositories(conn).webhook_inbox.get(event_id)


async def test_exact_bytes_committed_and_duplicate_is_noop(test_db_url):
    async with Database(test_db_url) as db:
        inbox = Inbox(db, Reader(), Verifier())
        app = FastAPI()
        app.include_router(build_router(inbox))
        raw = raw_event()
        headers = {"paypal-transmission-id": "tx-1", "Authorization": "never-store-me"}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            responses = await asyncio.gather(
                *(client.post("/webhooks/paypal", content=raw, headers=headers) for _ in range(2))
            )
        assert [r.status_code for r in responses] == [200, 200]
        event = await stored(db)
        assert bytes(event["raw_body"]) == raw
        assert event["headers"] == {"paypal-transmission-id": "tx-1"}
        assert event["signature"] == "UNVERIFIED" and event["status"] == "RECEIVED"
        assert not await inbox.receive(raw_event(event_type="FORGED.OVERWRITE"), {})
        assert (await stored(db))["event_type"] == "CUSTOMER.DISPUTE.CREATED"


async def test_storage_failure_returns_503():
    class FailingInbox:
        async def receive(self, raw, headers):
            raise RuntimeError("private driver failure")

    app = FastAPI()
    app.include_router(build_router(FailingInbox()))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/webhooks/paypal", content=raw_event())
    assert response.status_code == 503
    assert "private" not in response.text


@pytest.mark.parametrize("raw", [b"[]", b"bad", b'{"id":"a","id":"b","event_type":"x"}'])
async def test_invalid_payload_returns_400(raw):
    app = FastAPI()
    app.include_router(build_router(Inbox(None, None, None)))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (await client.post("/webhooks/paypal", content=raw)).status_code == 400


@pytest.mark.parametrize("initial_state", ["OPEN", "SCHEDULED"])
async def test_unknown_retried_and_only_success_gets_and_reserves(test_db_url, initial_state):
    async with Database(test_db_url) as db:
        await seed_module.seed_marketplace(db, scenario())
        async with db.tx() as conn:
            await conn.execute("UPDATE obligations SET state=%s", (initial_state,))
        verifier = Verifier(Verification.UNKNOWN, Verification.SUCCESS)
        reader = Reader(db)
        inbox = Inbox(db, reader, verifier)
        await inbox.receive(raw_event(), {"paypal-transmission-id": "t1"})
        assert await inbox.process_one()
        assert (await stored(db))["status"] == "RECEIVED"
        assert not reader.calls
        assert await inbox.process_one()
        event = await stored(db)
        assert event["status"] == "PROCESSED" and event["signature"] == "SUCCESS"
        assert event["attempts"] == 2
        assert verifier.calls[0][0] == raw_event()
        async with db.tx() as conn:
            repos = Repositories(conn)
            held = await repos.reservations.find()
            assert len(held) == 1 and held[0]["amount_minor"] == 36000
            assert held[0]["affects_cash"] is False
            obligation = await repos.obligations.get(held[0]["obligation_id"])
            assert obligation["state"] == "RESERVED" and obligation["version"] == 2
        assert not await inbox.process_one()


async def test_two_workers_claim_once_and_foreign_capture_cannot_reserve(test_db_url):
    async with Database(test_db_url) as db:
        await seed_module.seed_marketplace(db, scenario())
        reader = Reader()
        inbox = Inbox(db, reader, Verifier())
        await inbox.receive(raw_event(), {})
        assert sorted(await asyncio.gather(inbox.process_one(), inbox.process_one())) == [
            False,
            True,
        ]
        assert reader.calls == ["PP-D-1"]
        reader.detail = {
            **reader.detail,
            "dispute_id": "PP-D-2",
            "disputed_transactions": [{"seller_transaction_id": "FOREIGN-CAPTURE"}],
        }
        raw = json.dumps(
            {
                "id": "WH-FOREIGN",
                "event_type": "CUSTOMER.DISPUTE.CREATED",
                "resource": {"dispute_id": "PP-D-2"},
            }
        ).encode()
        await inbox.receive(raw, {})
        await inbox.process_one()
        assert (await stored(db, "WH-FOREIGN"))["status"] == "ERROR"
        async with db.tx() as conn:
            assert len(await Repositories(conn).reservations.find()) == 1


async def test_failed_signature_is_rejected_without_get(test_db_url):
    async with Database(test_db_url) as db:
        reader = Reader()
        inbox = Inbox(db, reader, Verifier(Verification.FAILURE))
        await inbox.receive(raw_event(), {})
        assert await inbox.process_one()
        event = await stored(db)
        assert event["status"] == "REJECTED" and event["signature"] == "FAILURE"
        assert not reader.calls
        assert not await inbox.process_one()


@pytest.mark.parametrize("state", ["OPEN", "SCHEDULED"])
async def test_earmark_and_transition_rollback_together(test_db_url, monkeypatch, state):
    async with Database(test_db_url) as db:
        await seed_module.seed_marketplace(db, scenario())
        async with db.tx() as conn:
            await conn.execute("UPDATE obligations SET state=%s", (state,))
        inbox = Inbox(db, Reader(db), Verifier())
        await inbox.receive(raw_event(), {})

        async def failed_cas(*args):
            raise RuntimeError("failed after earmark")

        monkeypatch.setattr("countersign.inbox.service.cas_transition", failed_cas)
        assert await inbox.process_one()
        assert (await stored(db))["status"] == "ERROR"
        async with db.tx() as conn:
            repos = Repositories(conn)
            assert not await repos.disputes.find()
            assert not await repos.reservations.find()
            assert all(row["state"] == state for row in await repos.obligations.find())


async def test_non_success_resource_get_never_changes_financial_state(test_db_url):
    async with Database(test_db_url) as db:
        await seed_module.seed_marketplace(db, scenario())
        reader = Reader()
        reader.status_code = 503
        inbox = Inbox(db, reader, Verifier())
        await inbox.receive(raw_event(), {})
        await inbox.process_one()
        assert (await stored(db))["status"] == "ERROR"
        async with db.tx() as conn:
            assert not await Repositories(conn).disputes.find()
            assert not await Repositories(conn).reservations.find()


async def test_reordered_resolved_event_keeps_reserve_and_deduplicates_earmark(test_db_url):
    async with Database(test_db_url) as db:
        await seed_module.seed_marketplace(db, scenario())
        reader = Reader()
        inbox = Inbox(db, reader, Verifier())
        await inbox.receive(raw_event(), {})
        await inbox.process_one()
        reader.detail.update(status="RESOLVED", dispute_outcome={"outcome_code": "UNVERIFIED-CODE"})
        await inbox.receive(raw_event("WH-2", "CUSTOMER.DISPUTE.RESOLVED"), {})
        await inbox.process_one()
        async with db.tx() as conn:
            repos = Repositories(conn)
            assert len(await repos.reservations.find()) == 1
            assert (await repos.disputes.find())[0]["phase"] == "UNKNOWN_OUTCOME"
            assert len(await repos.incidents.find()) == 1
            assert (await repos.reservations.find())[0]["state"] == "ACTIVE"


async def test_seed_is_atomic_and_rerun_does_not_overwrite_owner(test_db_url):
    async with Database(test_db_url) as db:
        data = scenario()
        result = await seed_module.seed_marketplace(db, data)
        assert result == {"created": True, "makers": 3, "orders": 4, "obligations": 4}
        async with db.tx() as conn:
            repos = Repositories(conn)
            maker = (await repos.makers.find())[0]
            await repos.makers.update(maker["id"], payout_email="owner-changed@example.test")
            assert sorted(row["amount_minor"] for row in await repos.obligations.find()) == [
                5400,
                30000,
                36000,
                48600,
            ]
        assert not (await seed_module.seed_marketplace(db, data))["created"]
        async with db.tx() as conn:
            assert (await Repositories(conn).makers.get(maker["id"]))[
                "payout_email"
            ] == "owner-changed@example.test"
        changed = deepcopy(data)
        changed["seed"] += 1
        with pytest.raises(ValueError, match="different scenario"):
            await seed_module.seed_marketplace(db, changed)


async def test_invalid_seed_leaves_no_partial_rows(test_db_url):
    async with Database(test_db_url) as db:
        data = scenario()
        data["initial_state"]["orders"][1]["maker"] = "unknown"
        with pytest.raises(KeyError):
            await seed_module.seed_marketplace(db, data)
        async with db.tx() as conn:
            repos = Repositories(conn)
            assert not await repos.makers.find()
            assert not await repos.orders.find()
            assert not await repos.obligations.find()


@pytest.mark.parametrize("timestamp", ["2026-11-02T09:00:00", 123, {"bad": "timestamp"}])
async def test_naive_or_non_string_event_timestamp_rejected(timestamp):
    inbox = Inbox(None, None, None)
    raw = json.dumps({"id": "BAD-TIME", "event_type": "ANY", "create_time": timestamp}).encode()
    from countersign.inbox import InboxError

    with pytest.raises(InboxError, match="Invalid webhook"):
        await inbox.receive(raw, {})


async def test_nested_batch_header_is_get_confirmed_without_changing_payouts(test_db_url):
    class BatchReader:
        def __init__(self):
            self.calls = []

        async def get_payout_batch(self, batch_id):
            self.calls.append(batch_id)
            return SimpleNamespace(
                status_code=200, body={"batch_header": {"payout_batch_id": batch_id}}
            )

    async with Database(test_db_url) as db:
        reader = BatchReader()
        inbox = Inbox(db, reader, Verifier())
        raw = json.dumps(
            {
                "id": "WH-BATCH",
                "event_type": "PAYMENT.PAYOUTSBATCH.SUCCESS",
                "resource": {"batch_header": {"payout_batch_id": "BATCH-1"}},
            }
        ).encode()
        await inbox.receive(raw, {})
        await inbox.process_one()
        assert reader.calls == ["BATCH-1"]
        assert (await stored(db, "WH-BATCH"))["status"] == "IGNORED"
        async with db.tx() as conn:
            assert not await Repositories(conn).payout_attempts.find()


@pytest.mark.parametrize(
    "table,values",
    [
        ("policies", {"key": "existing-policy", "value": 90}),
        ("payout_runs", {"week_start": "2026-11-02", "state": "DRAFT"}),
    ],
)
async def test_seed_refuses_unmarked_financial_state(test_db_url, table, values):
    async with Database(test_db_url) as db:
        async with db.tx() as conn:
            await Repositories(conn)[table].insert(**values)
        with pytest.raises(ValueError, match="empty marketplace"):
            await seed_module.seed_marketplace(db, scenario())
        async with db.tx() as conn:
            assert not await Repositories(conn).makers.find()


@pytest.mark.parametrize("threshold", [None, 3600])
async def test_demo_script_deadline_default_and_owner_override_survive_reseed(test_db_url, threshold):
    data = scenario()
    if threshold is None:
        data["initial_state"]["policy"].pop("deadline_close_seconds", None)
    else:
        data["initial_state"]["policy"]["deadline_close_seconds"] = threshold
    async with Database(test_db_url) as db:
        await seed_module.seed_marketplace(db, data)
        async with db.tx() as conn:
            repos = Repositories(conn)
            assert (await repos.policies.get("deadline_close_seconds"))["value"] == (172800 if threshold is None else threshold)
            await repos.policies.update("deadline_close_seconds", value=7200)
        await seed_module.seed_marketplace(db, data)
        async with db.tx() as conn:
            assert (await Repositories(conn).policies.get("deadline_close_seconds"))["value"] == 7200
