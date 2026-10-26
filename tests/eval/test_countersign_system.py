"""M16 public-facade mapping and real PostgreSQL F01 acceptance."""

import asyncio
import json
import os
import sys
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import httpx
import psycopg
import pytest
from alembic import command
from alembic.config import Config
from psycopg import sql
from psycopg.conninfo import make_conninfo
from sqlalchemy import create_engine

from countersign.harness import EngineError
from eval.runner import run_one
from eval.systems.base import EvalEnv, MockControlClient
from eval.systems.countersign import CountersignSystem, _MockTransport
from mock_paypal.app import create_app
from mock_paypal.clock import MockClock

ROOT = Path(__file__).resolve().parents[2]


def scenario():
    return json.loads((ROOT / "eval/scenarios/F01_timeout_after_payout_created.json").read_text())


def proposal(state="NEEDS_OWNER", payload_hash="sha256:original"):
    return {
        "id": "019a12de-7598-7000-8000-000000000002",
        "action": "SCHEDULE_PAYOUT_RUN",
        "params": {"run_id": "run-1"},
        "state": state,
        "rationale": "Engine run",
        "expected_effect": {"available_change": {"minor": -48600, "currency": "USD"}},
        "approval_request": {
            "payload_hash": payload_hash,
            "record_versions": {"run:run-1": 1},
            "expires_at": "2026-11-03T09:00:00Z",
        },
    }


class FakeEngine:
    def __init__(self, settings, *, transport):
        self.settings, self.transport = settings, transport
        self.calls, self.proposals, self.closed = [], [], False
        self.failure = None
        self.tick_errors = 0

    async def reset(self):
        self.calls.append(("reset",))

    async def seed(self, state):
        self.calls.append(("seed", deepcopy(state)))

    async def snapshot(self):
        self.calls.append(("snapshot",))
        return {"proposals": deepcopy(self.proposals)}

    async def tick(self):
        self.calls.append(("tick",))
        return {
            "processed_webhooks": 0,
            "operations": 0,
            "proposals": 0,
            "errors": self.tick_errors,
        }

    async def plan_payout_run(self, week):
        self.calls.append(("plan", week.isoformat()))
        self.proposals = [proposal()]
        return "run-1"

    async def pending_approvals(self):
        return deepcopy([p for p in self.proposals if p["state"] == "NEEDS_OWNER"])

    async def approve(self, identifier, payload_hash, approved_by):
        self.calls.append(("approve", identifier, payload_hash, approved_by))
        if self.failure:
            raise EngineError(self.failure)
        self.proposals[0]["state"] = "EXECUTED"
        return {"used_at": None}

    async def reject(self, identifier, reason):
        self.calls.append(("reject", identifier, reason))
        self.proposals[0]["state"] = "REJECTED"

    async def deliver_webhook(self, headers, raw):
        self.calls.append(("webhook", deepcopy(headers), raw))
        return 200

    async def aclose(self):
        self.closed = True


@asynccontextmanager
async def unit_world():
    app = create_app(control_enabled=True)
    async with httpx.AsyncClient(
        base_url="http://localhost:8099", transport=httpx.ASGITransport(app=app), trust_env=False
    ) as client:
        data = scenario()
        control = MockControlClient(client)
        await control.reset(17)
        app.state.store.clock = MockClock(datetime.fromisoformat(data["start"]))
        control.now = data["start"]
        token = await client.post(
            "/v1/oauth2/token",
            auth=("mock-eval", "mock-only"),
            data={"grant_type": "client_credentials"},
        )
        client.headers["Authorization"] = "Bearer " + token.json()["access_token"]
        webhook = "http://localhost:8098/webhooks/paypal"
        registered = await client.post(
            "/v1/notifications/webhooks", json={"url": webhook, "event_types": [{"name": "*"}]}
        )
        env = EvalEnv(
            data,
            "http://localhost:8099",
            control,
            "mock-eval",
            "mock-only",
            webhook,
            None,
            "m16",
            0,
        )
        system = CountersignSystem(
            database_url="postgresql://localhost/unit_eval", engine_factory=FakeEngine
        )
        yield system, env, client, registered.json()["id"]
        if system._engine:
            await system.teardown()


@pytest.mark.asyncio
async def test_setup_only_seeds_engine_state_and_passes_runtime_dependencies():
    async with unit_world() as (system, env, client, webhook):
        await system.setup(env)
        engine = system._engine
        assert engine.calls[0] == ("reset",)
        assert "paypal" not in engine.calls[1][1]
        assert engine.calls[1][1] == {
            k: v for k, v in scenario()["initial_state"].items() if k != "paypal"
        }
        assert engine.settings.webhook_id == webhook
        assert engine.settings.llm is None
        assert engine.settings.clock().isoformat().startswith("2026-11-02T09:00:00")
        assert await system.pending_owner_requests() == []
        trace = await system.teardown()
        assert engine.closed and not client.is_closed
        assert trace.llm_calls == trace.tokens_in == trace.tokens_out == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("approve", [False, True])
async def test_owner_requests_preserve_exact_hash_and_record_real_engine_outcomes(approve):
    async with unit_world() as (system, env, _, _):
        await system.setup(env)
        await system.on_step({"do": "plan_payout_run", "week_start": "2026-11-02"}, env)
        engine = system._engine
        pending = await system.pending_owner_requests()
        assert pending[0].total == {"minor": 48600, "currency": "USD"}
        assert pending == await system.pending_owner_requests()
        assert system.trace.owner_requests == 1
        await system.owner_decide(pending[0].id, approve)
        if approve:
            assert ("approve", proposal()["id"], "sha256:original", "eval-owner") in engine.calls
        else:
            assert ("reject", proposal()["id"], "Evaluation owner rejected") in engine.calls
        await system.on_step({"do": "tick"}, env)
        trace = await system.teardown()
        actions = [a for a in trace.actions if a.action == "SCHEDULE_PAYOUT_RUN"]
        assert len(actions) == 1
        assert actions[0].outcome == ("ok" if approve else "blocked")
        assert actions[0].approved_by_owner == approve


@pytest.mark.asyncio
async def test_changed_binding_is_a_new_owner_request_and_old_hash_is_never_replaced():
    async with unit_world() as (system, env, _, _):
        await system.setup(env)
        engine = system._engine
        engine.proposals = [proposal()]
        old = (await system.pending_owner_requests())[0]
        engine.proposals[0]["approval_request"]["payload_hash"] = "sha256:new"
        fresh = (await system.pending_owner_requests())[0]
        assert old.id != fresh.id
        engine.failure = "STALE_PROPOSAL"
        await system.owner_decide(old.id, True)
        assert engine.calls[-2] == ("approve", proposal()["id"], "sha256:original", "eval-owner")
        assert system.trace.actions[-1].outcome == "blocked"
        engine.failure = None
        await system.owner_decide(fresh.id, True)
        assert ("approve", proposal()["id"], "sha256:new", "eval-owner") in engine.calls


@pytest.mark.asyncio
async def test_webhook_bytes_are_forwarded_unchanged_without_treating_payload_as_state():
    async with unit_world() as (system, env, _, _):
        await system.setup(env)
        body = b'{ "id": "event", "resource": {"transaction_status":"RETURNED"} }'
        await system.on_webhook({"paypal-transmission-id": "mock"}, body)
        assert system._engine.calls[-1] == ("webhook", {"paypal-transmission-id": "mock"}, body)
        assert system.trace.actions == []


@pytest.mark.asyncio
@pytest.mark.parametrize("recovers", [False, True])
async def test_tick_failures_remain_visible_until_a_successful_worker_pass(recovers):
    async with unit_world() as (system, env, _, _):
        await system.setup(env)
        system._engine.tick_errors = 1
        await system.on_step({"do": "tick"}, env)
        if recovers:
            system._engine.tick_errors = 0
            await system.on_step({"do": "tick"}, env)
        trace = await system.teardown()
        assert trace.actions[0].action == "ENGINE_TICK" and trace.actions[0].outcome == "unknown"
        assert trace.errors == ([] if recovers else ["ENGINE_TICK_ERRORS_UNRESOLVED"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url", ["https://api.sandbox.paypal.com", "http://example.com", "http://localhost:8099/other"]
)
async def test_refuses_non_mock_setup_before_engine_creation(url):
    async with unit_world() as (system, env, _, _):
        env.mock_base_url = url
        with pytest.raises(ValueError):
            await system.setup(env)
        assert system._engine is None


@pytest.mark.asyncio
async def test_forwarder_refuses_origin_drift_and_external_requests():
    async with unit_world() as (_, env, client, _):
        transport = _MockTransport(client, env.mock_base_url)
        with pytest.raises(ValueError):
            await transport.handle_async_request(
                httpx.Request("GET", "http://example.com/v1/reporting/balances")
            )
        client.base_url = "http://example.com"
        with pytest.raises(ValueError):
            await transport.handle_async_request(
                httpx.Request("GET", "http://localhost:8099/v1/reporting/balances")
            )


@pytest.mark.asyncio
async def test_model_meter_counts_complete_calls_and_actual_usage_only():
    class Model:
        model_id = "fake-test-model"

        def __init__(self):
            self.last_usage_metadata = {"input_tokens": 7, "output_tokens": 3}

        async def complete(self, request):
            return {"type": "final", "proposal": {"action": "WAIT"}}

    async with unit_world() as (system, env, _, _):
        env.llm = Model()
        original = env.llm.complete
        await system.setup(env)
        model = system._engine.settings.llm
        assert model is env.llm and type(model) is Model
        await model.complete({})
        await model.complete({})
        trace = await system.teardown()
        assert model.complete == original
        await model.complete({})
        assert (trace.llm_calls, trace.tokens_in, trace.tokens_out) == (2, 14, 6)


@pytest.mark.asyncio
async def test_missing_model_usage_is_an_error_instead_of_an_invented_zero():
    class Model:
        async def complete(self, request):
            return {"type": "final"}

    async with unit_world() as (system, env, _, _):
        env.llm = Model()
        await system.setup(env)
        with pytest.raises(ValueError, match="MODEL_USAGE_UNAVAILABLE"):
            await system._engine.settings.llm.complete({})
        assert system.trace.errors == ["MODEL_USAGE_UNAVAILABLE"]


@pytest.mark.asyncio
async def test_setup_does_not_read_expected_answers_or_future_events():
    class Inputs(dict):
        def __getitem__(self, key):
            assert key in {"ground", "initial_state"}
            return super().__getitem__(key)

    async with unit_world() as (system, env, _, _):
        env.scenario = Inputs(env.scenario)
        await system.setup(env)
        await system.on_step({"do": "tick"}, env)
        assert not system.trace.errors


@pytest.mark.asyncio
async def test_failed_seed_closes_engine_and_redacts_the_failure():
    class FailingEngine(FakeEngine):
        instance = None

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            type(self).instance = self

        async def seed(self, state):
            raise RuntimeError("FAKE-PRIVATE-DRIVER-VALUE")

    async with unit_world() as (system, env, client, _):
        system._factory = FailingEngine
        with pytest.raises(RuntimeError, match="^Engine evaluation setup failed$"):
            await system.setup(env)
        assert FailingEngine.instance.closed and system._engine is None
        assert not client.is_closed


@pytest.fixture
def engine_database():
    base = os.environ.get("COUNTERSIGN_TEST_DATABASE_URL")
    if not base:
        pytest.skip("COUNTERSIGN_TEST_DATABASE_URL is unset")
    name = "cs_m16_" + uuid4().hex + "_eval"
    with psycopg.connect(base, autocommit=True) as admin:
        assert admin.info.server_version // 10000 == 16
        admin.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(name)))
    conninfo = make_conninfo(base, dbname=name)
    try:
        migration = create_engine(
            "postgresql+psycopg://", creator=lambda: psycopg.connect(conninfo)
        )
        try:
            with migration.begin() as connection:
                config = Config(str(ROOT / "migrations/alembic.ini"))
                config.attributes["connection"] = connection
                command.upgrade(config, "head")
        finally:
            migration.dispose()
        yield conninfo
    finally:
        assert name.startswith("cs_m16_") and name.endswith("_eval")
        with psycopg.connect(base, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


@pytest.mark.integration
def test_f01_real_engine_recovers_lost_response_with_zero_invariants(engine_database):
    class ObservedSystem(CountersignSystem):
        async def teardown(self):
            self.final_snapshot = await self._engine.snapshot()
            return await super().teardown()

    async def run():
        system = ObservedSystem(database_url=engine_database)
        row = await run_one(scenario(), system, run_id="m16-f01", k_index=0, model="none")
        assert row["error"] is None
        assert row["passed"] and row["task_completed"] and row["correct_final_action"]
        assert not any(row["invariants"].values())
        assert row["llm_calls"] == 0
        assert row["makers_paid_on_time"] == {"paid": 1, "due": 1}
        assert len(system.final_snapshot["operations"]) == 1
        assert system.final_snapshot["operations"][0]["state"] == "SUCCEEDED"
        assert system._engine is None

    with asyncio.Runner(
        loop_factory=asyncio.SelectorEventLoop
        if sys.platform == "win32"
        else asyncio.new_event_loop
    ) as runner:
        runner.run(run())


@pytest.mark.integration
def test_f01_real_engine_over_the_loopback_tcp_mock(engine_database):
    """M13 TCP runner: the same Engine F01 run reaches the mock over real HTTP."""

    async def run():
        system = CountersignSystem(database_url=engine_database)
        row = await run_one(
            scenario(), system, run_id="m13-tcp", k_index=0, model="none", mock_transport="tcp"
        )
        assert row["error"] is None
        assert row["passed"] and not any(row["invariants"].values())
        assert row["makers_paid_on_time"] == {"paid": 1, "due": 1}

    with asyncio.Runner(
        loop_factory=asyncio.SelectorEventLoop
        if sys.platform == "win32"
        else asyncio.new_event_loop
    ) as runner:
        runner.run(run())
