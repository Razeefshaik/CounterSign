"""Facade contracts against isolated PostgreSQL and the in-process PayPal mock."""

import asyncio
import importlib.util
import json
import os
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from psycopg import sql
from psycopg.conninfo import make_conninfo
from sqlalchemy import create_engine

from countersign.config import PayPalSettings, Settings
from countersign.db import Database, Repositories
from countersign.executor import Executor, ExecutorError
from countersign.harness import Engine, EngineError, EngineSettings
from countersign.main import create_app
from countersign.paypal.http import PayPalHTTP
from countersign.worker import (
    WorkerSettings,
    WorkerSettingsError,
    load_worker_settings,
    run_workers,
)
from mock_paypal.app import create_app as create_mock

ROOT = Path(__file__).resolve().parents[2]
BASE = "http://localhost:8099"
NOW = datetime(2026, 11, 2, 9, tzinfo=UTC)
WEEK = date(2026, 11, 2)
pytestmark = pytest.mark.integration


def initial_state():
    scenario = json.loads(
        (ROOT / "eval/scenarios/F01_timeout_after_payout_created.json").read_text()
    )
    state = deepcopy(scenario["initial_state"])
    paypal = state.pop("paypal")
    return state, paypal


def settings(conninfo, clock):
    return EngineSettings(
        database_url=conninfo,
        paypal_base_url=BASE,
        paypal_client_id="mock-client",
        paypal_client_secret="mock-secret",
        webhook_id="WH-TEST",
        clock=clock,
        llm=None,
    )


@pytest_asyncio.fixture
async def world(db_schema):
    mock = create_mock(control_enabled=True)
    transport = httpx.ASGITransport(app=mock)
    state, paypal = initial_state()
    async with (
        Database(db_schema.conninfo, max_size=6) as database,
        httpx.AsyncClient(transport=transport, base_url=BASE) as controls,
        PayPalHTTP(PayPalSettings(base_url=BASE), transport=transport) as http,
    ):
        assert (await controls.post("/_mock/seed", json=paypal)).status_code == 200
        config = settings(db_schema.conninfo, lambda: mock.state.store.clock.now)
        executor = Executor.from_http(database, http, clock=config.clock)
        async with Engine(config, database=database, http=http) as engine:
            yield SimpleNamespace(
                engine=engine,
                database=database,
                controls=controls,
                mock=mock,
                transport=transport,
                settings=config,
                state=state,
                schema=db_schema,
                http=http,
                executor=executor,
            )
        assert not database.pool.closed


async def ready_poll(engine):
    async with asyncio.timeout(60):
        while True:
            result = await engine.tick()
            if result["errors"] == 0:
                return result
            await asyncio.sleep(0.02)


def validate(document, name):
    spec = importlib.util.spec_from_file_location(
        "harness_schema_check", ROOT / "tests/dashboard/schema_check.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    schema = json.loads((ROOT / "tests/dashboard/schemas" / (name + ".json")).read_text())

    # Fixture-derived schemas overfit a sample incident link and an empty version
    # map. PLAN makes incident_id nullable; CONTRACTS binds arbitrary record IDs.
    # The owner-approval field exists only in NEEDS_OWNER (CONTRACTS 2.3), and
    # reservation links are nullable in PLAN 2.2. Report these fixture overfits.
    def contract_nullable_fields(node):
        if isinstance(node, dict):
            properties = node.get("properties", {})
            if "incident_id" in properties:
                properties["incident_id"]["type"] = ["string", "null"]
            if "record_versions" in properties:
                properties["record_versions"]["additionalProperties"] = True
            if "approval_request" in properties and "approval_request" in node.get("required", []):
                node["required"] = [
                    field for field in node["required"] if field != "approval_request"
                ]
            if name == "reservations":
                for field in ("obligation_id", "dispute_id", "proposal_id", "run_id"):
                    if field in properties:
                        properties[field]["type"] = ["string", "null"]
            for value in node.values():
                contract_nullable_fields(value)
        elif isinstance(node, list):
            for item in node:
                contract_nullable_fields(item)

    contract_nullable_fields(schema)
    module.validate(document, schema)
    if name == "proposals":
        for proposal in document["items"]:
            assert ("approval_request" in proposal) == (proposal["state"] == "NEEDS_OWNER")


@pytest.mark.asyncio
async def test_public_facade_lost_response_recovers_and_settles_once(world):
    e = world.engine
    await e.seed(world.state)
    tick = await ready_poll(e)
    assert set(tick) == {"processed_webhooks", "proposals", "operations", "errors"}
    run_id = await e.plan_payout_run(WEEK)
    assert UUID(run_id).version == 7
    pending = await e.pending_approvals()
    assert len(pending) == 1
    validate({"items": pending, "next_cursor": None}, "proposals")
    proposal = pending[0]
    versions = proposal["approval_request"]["record_versions"]
    assert versions and all(type(value) is int and value > 0 for value in versions.values())
    assert proposal["params"]["run_id"] == run_id
    assert "maker-b@example.com" not in json.dumps(proposal)
    approval = await e.approve(proposal["id"], proposal["approval_request"]["payload_hash"])
    assert approval["used_at"] is None
    await world.controls.post(
        "/_mock/faults",
        json={
            "match": {"method": "POST", "path": "^/v1/payments/payouts$"},
            "action": {"type": "drop_response", "after_commit": True},
        },
    )
    first = await e.tick()
    assert first["operations"] == 1 and first["proposals"] == 1
    truth = (await world.controls.get("/_mock/ground-truth")).json()
    assert len(truth["payout_items"]) == 1
    assert (await e.pending_approvals()) == []
    await world.controls.post("/_mock/clock/advance", json={"seconds": 60})
    await ready_poll(e)
    await ready_poll(e)
    snapshot = await e.snapshot()
    for key, schema in (
        ("incidents", "incidents"),
        ("proposals", "proposals"),
        ("runs", "payout-runs"),
        ("makers", "makers"),
        ("disputes", "disputes"),
        ("reservations", "reservations"),
    ):
        validate({"items": snapshot[key], "next_cursor": None}, schema)
    validate(snapshot["cash"], "cash")
    assert snapshot["runs"][0]["id"] == run_id
    async with world.database.tx() as conn:
        repos = Repositories(conn)
        assert [o["state"] for o in await repos.obligations.find()] == ["SETTLED"]
        assert len(await repos.payout_attempts.find()) == 1
        assert len(await repos.operations.find()) == 1
        journals = await repos.ledger_journals.find()
        assert sum(j["memo"].startswith("PAYOUT_SUCCESS:") for j in journals) == 1
    assert len((await world.controls.get("/_mock/ground-truth")).json()["payout_items"]) == 1
    assert "maker-b@example.com" not in json.dumps(snapshot)
    assert all("payload" not in operation for operation in snapshot["operations"])


@pytest.mark.asyncio
async def test_reject_returns_public_document_and_releases_obligations(world):
    await world.engine.seed(world.state)
    await ready_poll(world.engine)
    await world.engine.plan_payout_run(WEEK)
    [proposal] = await world.engine.pending_approvals()
    rejected = await world.engine.reject(proposal["id"], "Defer")
    assert rejected["state"] == "REJECTED" and "raw_output" not in rejected
    assert not await world.engine.pending_approvals()
    async with world.database.tx() as conn:
        assert [r["state"] for r in await Repositories(conn).obligations.find()] == ["OPEN"]


@pytest.mark.asyncio
async def test_seed_duplicate_and_bad_reference_roll_back(world):
    invalid = deepcopy(world.state)
    invalid["orders"][0]["maker"] = "MISSING"
    with pytest.raises(EngineError):
        await world.engine.seed(invalid)
    async with world.database.tx() as conn:
        assert not await Repositories(conn).makers.find()
    await world.engine.seed(world.state)
    # Repeating the same seed is idempotent and never resets later owner work.
    await world.engine.seed(world.state)
    async with world.database.tx() as conn:
        assert len(await Repositories(conn).makers.find()) == 1


@pytest.mark.asyncio
async def test_facade_delivers_durable_webhook_and_rejects_forgery(world):
    await world.engine.seed(world.state)
    emitted = world.mock.state.webhook_service
    # Registration is test setup only; the facade never calls a control endpoint.
    registration = await world.controls.post(
        "/v1/oauth2/token",
        data={"grant_type": "client_credentials"},
        auth=("mock-client", "mock-secret"),
    )
    token = registration.json()["access_token"]
    registration = await world.controls.post(
        "/v1/notifications/webhooks",
        headers={"Authorization": "Bearer " + token},
        json={"url": "http://localhost:8000/webhooks/paypal", "event_types": [{"name": "*"}]},
    )
    webhook_id = registration.json()["id"]
    # Use an engine configured with this generated mock registration.
    config = replace(world.settings, webhook_id=webhook_id)
    async with Engine(
        config, database=world.database, http=world.http, executor=world.executor
    ) as e:
        event = emitted.emit("CUSTOMER.DISPUTE.CREATED", {"dispute_id": "PP-D-FORGED"})
        record = emitted.store.webhooks.events[event["id"]]
        headers = emitted.headers(record, webhook_id)
        headers["paypal-transmission-sig"] = "forged"
        assert await e.deliver_webhook(headers, record["raw"]) == 200
        assert await e.deliver_webhook(headers, record["raw"]) == 200
        result = await e.tick()
        assert result["processed_webhooks"] == 1
        async with world.database.tx() as conn:
            rows = await Repositories(conn).webhook_inbox.find()
            assert len(rows) == 1 and rows[0]["status"] == "REJECTED"
            assert bytes(rows[0]["raw_body"]) == record["raw"]
        assert (await e.tick())["processed_webhooks"] == 0
        assert await e.deliver_webhook({}, b"not json") == 400


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://localhost/countersign",
        "postgresql://localhost/countersign?application_name=x_test",
        "dbname=countersign application_name=looks_eval",
    ],
)
@pytest.mark.asyncio
async def test_reset_refuses_non_test_database_before_touching_it(url):
    engine = Engine(settings(url, lambda: NOW))
    with pytest.raises(EngineError):
        await engine.reset()
    await engine.aclose()


@pytest.mark.asyncio
async def test_reset_checks_injected_connection_database_too(world):
    fake = make_conninfo(world.settings.database_url, dbname="not_the_connected_database_eval")
    async with Engine(
        settings(fake, world.settings.clock),
        database=world.database,
        http=world.http,
        executor=world.executor,
    ) as engine:
        with pytest.raises(EngineError):
            await engine.reset()


@pytest.fixture
def reset_database():
    base = os.environ.get("COUNTERSIGN_TEST_DATABASE_URL")
    if not base:
        pytest.skip("COUNTERSIGN_TEST_DATABASE_URL is unset")
    name = "cs_harness_" + uuid4().hex + "_test"
    with psycopg.connect(base, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(name)))
    conninfo = make_conninfo(base, dbname=name, options="-csearch_path=facade_test,public")
    try:
        with psycopg.connect(conninfo, autocommit=True) as conn:
            conn.execute("CREATE SCHEMA facade_test")
            conn.execute("CREATE SCHEMA untouched")
            conn.execute("CREATE TABLE untouched.sentinel(value text)")
            conn.execute("INSERT INTO untouched.sentinel VALUES ('preserved')")
        migration_engine = create_engine(
            "postgresql+psycopg://", creator=lambda: psycopg.connect(conninfo)
        )
        try:
            with migration_engine.begin() as connection:
                config = Config(str(ROOT / "migrations/alembic.ini"))
                config.attributes.update(connection=connection, version_table_schema="facade_test")
                command.upgrade(config, "head")
        finally:
            migration_engine.dispose()
        yield conninfo
    finally:
        # Only the randomly named database created by this fixture can be dropped.
        assert name.startswith("cs_harness_") and name.endswith("_test")
        with psycopg.connect(base, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


@pytest.mark.asyncio
async def test_reset_empties_only_app_tables_and_preserves_migrations_and_other_schema(
    reset_database,
):
    mock = create_mock(control_enabled=True)
    async with (
        Database(reset_database) as database,
        PayPalHTTP(PayPalSettings(base_url=BASE), transport=httpx.ASGITransport(app=mock)) as http,
        Engine(
            settings(reset_database, lambda: NOW),
            database=database,
            http=http,
            executor=Executor.from_http(database, http),
        ) as engine,
    ):
        async with database.tx() as conn:
            await Repositories(conn).makers.insert(display_name="Test")
        await engine.reset()
        async with database.tx() as conn:
            assert not await Repositories(conn).makers.find()
            assert (
                await (await conn.execute("SELECT version_num FROM alembic_version")).fetchone()
            )["version_num"] == "0004"
            assert (await (await conn.execute("SELECT value FROM untouched.sentinel")).fetchone())[
                "value"
            ] == "preserved"


@pytest.mark.parametrize("value", [0, -1, True, float("nan"), float("inf")])
def test_worker_intervals_reject_invalid_values(value):
    with pytest.raises(WorkerSettingsError):
        WorkerSettings(reconcile_interval=value)


def test_worker_settings_load_at_runtime_and_hide_invalid_values(monkeypatch):
    monkeypatch.setenv("COUNTERSIGN_RECONCILE_INTERVAL_SECONDS", "30")
    assert load_worker_settings().reconcile_interval == 30
    monkeypatch.setenv("COUNTERSIGN_EXECUTOR_INTERVAL_SECONDS", "private-input")
    with pytest.raises(WorkerSettingsError) as error:
        load_worker_settings()
    assert "private-input" not in str(error.value)


@pytest.mark.asyncio
async def test_worker_shutdown_cancels_pending_io_without_closing_dependencies(monkeypatch):
    started, cancelled, stop = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def pending():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    async def harmless(*args, **kwargs):
        return {"errors": 0, "proposals": 0, "operations": 0}

    monkeypatch.setattr("countersign.worker._execute_and_sweep", harmless)
    task = asyncio.create_task(
        run_workers(
            None,
            SimpleNamespace(process_one=pending),
            SimpleNamespace(poll_once=pending),
            None,
            stop,
        )
    )
    await asyncio.wait_for(started.wait(), 1)
    stop.set()
    await asyncio.wait_for(task, 1)
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_main_starts_reconciliation_and_executor_sweep_using_the_factory(world, monkeypatch):
    swept, reconciled = asyncio.Event(), asyncio.Event()
    original = Executor.sweep

    async def sweep(self):
        swept.set()
        return await original(self)

    class Poller:
        async def poll_once(self):
            reconciled.set()

    class Verifier:
        async def verify(self, raw, headers):
            raise AssertionError("Empty inbox must not verify anything")

    monkeypatch.setattr(Executor, "sweep", sweep)
    app = create_app(
        settings=Settings("postgresql://fake:fake@localhost/fake"),
        database=world.database,
        http=world.http,
        verifier=Verifier(),
        reconciler=Poller(),
        worker_settings=WorkerSettings(
            inbox_interval=0.01, reconcile_interval=0.01, executor_interval=0.01
        ),
    )
    async with app.router.lifespan_context(app):
        await asyncio.wait_for(asyncio.gather(swept.wait(), reconciled.wait()), timeout=5)
        assert isinstance(app.state.executor, Executor)
    assert not world.database.pool.closed
    assert not world.http._client.is_closed


@pytest.mark.parametrize("action", ["RESERVE_EARNINGS", "RELEASE_RESERVE", "DEFER_SCHEDULED_PAYOUT"])
@pytest.mark.asyncio
async def test_nonpaying_actions_are_deferred_without_reimplementation(world, action):
    class Unsupported:
        def __init__(self):
            self.called = []
            self.swept = 0

        async def status(self, identifier):
            return {"proposal_state": "ALLOWED", "operations": {}}

        async def execute(self, identifier):
            self.called.append(identifier)
            raise ExecutorError("UNSUPPORTED_ACTION")

        async def sweep(self):
            self.swept += 1
            return {"recovered": 0, "resolved": {}}

    fake = Unsupported()
    async with world.database.tx() as conn:
        proposal = await Repositories(conn).proposals.insert(
            action=action, params={}, evidence_refs=[], model="test", raw_output={}, state="ALLOWED"
        )
    async with Engine(
        world.settings, database=world.database, http=world.http, executor=fake
    ) as engine:
        result = await engine.tick()
    assert fake.called == [] and fake.swept == 1
    assert result["errors"] == 0 and result["operations"] == 0
    async with world.database.tx() as conn:
        assert not await Repositories(conn).operations.find()
        assert (await Repositories(conn).proposals.get(proposal["id"]))["state"] == "ALLOWED"


@pytest.mark.asyncio
async def test_snapshot_never_returns_an_older_complete_balance_after_cash_guard(world):
    await world.engine.seed(world.state)
    assert (await world.engine.snapshot())["cash"] is not None
    async with world.database.tx() as conn:
        await Repositories(conn).balance_snapshots.insert(
            fetched_at=NOW.replace(year=2027),
            currency="USD",
            available_minor=None,
            withheld_minor=0,
            total_minor=150000,
            as_of_time=NOW,
            last_refresh_time=NOW,
            raw={"reconciliation": "REFUND_FUNDING_UNVERIFIED"},
        )
    assert (await world.engine.snapshot())["cash"] is None


def test_engine_settings_mask_values_and_require_explicit_agent_configuration():
    configured = settings("postgresql://private-id:private-secret@localhost/demo_eval", lambda: NOW)
    assert "private" not in repr(configured)
    assert "mock-client" not in repr(configured)
    with pytest.raises(EngineError, match="MODEL_CONFIGURATION_REQUIRED"):
        replace(configured, llm=object())
    with pytest.raises(EngineError, match="INVALID_ENGINE_SETTINGS"):
        replace(configured, paypal_base_url="https://api-m.paypal.com")


@pytest.mark.parametrize("links", [None, [], ["o1051"]])
@pytest.mark.asyncio
async def test_prior_payout_links_are_explicit_and_history_is_not_replayed(world, links):
    state = deepcopy(world.state)
    prior = {
        "key": "old-B",
        "maker": "B",
        "amount": {"minor": 48600, "currency": "USD"},
        "status": "SUCCESS",
        "payout_item_id": "MOCKI-OLD",
    }
    if links is not None:
        prior["order_keys"] = links
    state["prior_payouts"] = [prior]
    _, paypal = initial_state()
    paypal["payout_items"] = [
        {
            "payout_item_id": "MOCKI-OLD",
            "payout_batch_id": "MOCKB-OLD",
            "sender_batch_id": "prior-old-b",
            "request_id": "actual-remote-request",
            "receiver": "maker-b@example.com",
            "amount": "486.00",
            "status": "SUCCESS",
            "created_at": "2026-10-30T09:00:00Z",
            "synthesised": True,
        }
    ]
    assert (await world.controls.post("/_mock/seed", json=paypal)).status_code == 200
    if links is None:
        with pytest.raises(EngineError, match="SEED_PRIOR_LINK_REQUIRED"):
            await world.engine.seed(state)
        async with world.database.tx() as conn:
            assert not await Repositories(conn).makers.find()
        return
    await world.engine.seed(state)
    async with world.database.tx() as conn:
        repos = Repositories(conn)
        obligation = (await repos.obligations.find({"kind": "ORDER_SHARE"}))[0]
        assert obligation["state"] == ("SETTLED" if links else "OPEN")
        [operation] = await repos.operations.find()
        assert operation["payload"]["import_only"] is True
        assert operation["payload"]["original_request_id_known"] is False
        assert operation["state"] == "SUCCEEDED"
        assert not await repos.approvals.find()
        assert not await repos.proposals.find()
    snapshot = await world.engine.snapshot()
    assert snapshot["runs"][0]["created_at"] == "2026-10-30T09:00:00Z"
    validate({"items": snapshot["runs"], "next_cursor": None}, "payout-runs")
    await ready_poll(world.engine)
    truth = (await world.controls.get("/_mock/ground-truth")).json()
    assert len(truth["payout_items"]) == 1
    assert not [
        r
        for r in truth["requests"]
        if r["method"] == "POST" and r["path"] == "/v1/payments/payouts"
    ]


@pytest.mark.asyncio
async def test_unknown_verification_is_pending_and_storage_failure_is_503(world, monkeypatch):
    await world.engine.seed(world.state)
    body = b'{"id":"WH-UNKNOWN","event_type":"CUSTOMER.DISPUTE.CREATED","resource":{"dispute_id":"PP-D-NO-SIGNATURE"}}'
    assert await world.engine.deliver_webhook({}, body) == 200
    for _ in range(2):
        assert (await world.engine.tick())["processed_webhooks"] == 0
    async with world.database.tx() as conn:
        [row] = await Repositories(conn).webhook_inbox.find()
        assert row["signature"] == "UNVERIFIED" and row["status"] == "RECEIVED"

    async def unavailable(*args):
        raise psycopg.OperationalError("fake-private-token private@example.test")

    monkeypatch.setattr("countersign.harness.Inbox.receive", unavailable)
    assert await world.engine.deliver_webhook({}, body) == 503
