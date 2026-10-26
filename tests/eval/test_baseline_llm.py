"""M13 acceptance with fake models only: no provider, key or network is used."""

import json
from contextlib import asynccontextmanager
from copy import deepcopy
from pathlib import Path

import httpx
import pytest

from countersign.agent.models import ChatModelConfig, LangChainModel, ModelError
from eval.metrics import instant
from eval.runner import ModelQuotaExhausted, run_one, run_suite
from eval.systems.base import EvalEnv, MockControlClient
from eval.systems.baseline_llm import TOOLS, BaselineLLMSystem
from mock_paypal.app import create_app
from mock_paypal.clock import MockClock

ROOT = Path(__file__).resolve().parents[2]
SCENARIOS = ROOT / "eval/scenarios"
F01 = "F01_timeout_after_payout_created"


def scenario(name=F01):
    return json.loads((SCENARIOS / (name + ".json")).read_text(encoding="utf-8"))


class Usage:
    """Per-call usage exactly as LangChainModel exposes it."""

    def __init__(self):
        self.calls, self.requests = 0, []
        self.last_usage_metadata = {}

    def meter(self, request):
        self.calls += 1
        self.requests.append(deepcopy(request))
        self.last_usage_metadata = {"input_tokens": 100 + self.calls, "output_tokens": 10}


def tool_call(model, name, args):
    return {"tool_calls": [{"id": f"call-{model.calls}", "name": name, "args": args}]}


class NaiveFake(Usage):
    """A reasonable naive operator, decided only from the request it receives."""

    model_id = "fake-naive"

    async def complete(self, request):
        self.meter(request)
        data, observations = request["data"], request["observations"]
        business = data["business"]
        if data["step"]["do"] == "plan_payout_run":
            if observations or data["owner_requests"]:
                return {"proposal_json": "Asked the owner to approve this week's run."}
            paid = {k for p in business["prior_payouts"] for k in p.get("order_keys", [])}
            disputed = {d["order"] for d in business["disputes"]}
            pct = business["policy"]["maker_share_pct"]
            lines = []
            for maker in business["makers"]:
                keys = [
                    o["key"]
                    for o in business["orders"]
                    if o["maker"] == maker["key"] and o["key"] not in paid | disputed
                ]
                value = sum(
                    (o["amount"]["minor"] * pct + 50) // 100
                    for o in business["orders"]
                    if o["key"] in keys
                )
                if maker["verified"] and value:
                    lines.append({"maker": maker["key"], "amount_minor": value, "order_keys": keys})
            if not lines:
                return {"proposal_json": "Nothing is owed this week."}
            return tool_call(
                self,
                "request_owner_approval",
                {
                    "action": "SCHEDULE_PAYOUT_RUN",
                    "total_minor": sum(line["amount_minor"] for line in lines),
                    "week_start": data["step"]["week_start"],
                    "lines": lines,
                },
            )
        settled = {
            a["args"]["reference"]
            for a in data["my_recent_actions"]
            if a["tool"] == "create_payout"
            and a["result"].get("status") in {"CREATED", "ALREADY_SUBMITTED"}
        }
        for observation in observations:
            if observation["untrusted_result"].get("status") in {"CREATED", "ALREADY_SUBMITTED"}:
                settled.add(observation["tool_call"]["args"]["reference"])
        for owner in data["owner_requests"]:
            if owner["status"] != "APPROVED" or owner["action"] != "SCHEDULE_PAYOUT_RUN":
                continue
            for line in owner["params"]["lines"]:
                reference = f"run-{owner['params']['week_start']}-{line['maker']}"
                if reference not in settled:
                    return tool_call(
                        self,
                        "create_payout",
                        {
                            "maker": line["maker"],
                            "amount_minor": line["amount"]["minor"],
                            "reference": reference,
                        },
                    )
        return {"proposal_json": "Nothing is due now."}


class Scripted(Usage):
    def __init__(self, turns):
        super().__init__()
        self.turns = list(turns)

    async def complete(self, request):
        self.meter(request)
        return deepcopy(self.turns.pop(0)) if self.turns else {"proposal_json": "Done."}


class InputsOnly(dict):
    def __getitem__(self, key):
        assert key in {"initial_state", "ground"}, (
            "Baseline may not inspect answers or future steps"
        )
        return super().__getitem__(key)


@asynccontextmanager
async def rig(model, data=None, base="http://localhost:8099", **env_changes):
    data = deepcopy(data or scenario())
    system = BaselineLLMSystem()
    wire = []

    async def capture(request):
        if request.method == "POST" and request.url.path == "/v1/payments/payouts":
            wire.append((json.loads(request.content), request.headers.get("paypal-request-id")))

    app = create_app(control_enabled=True)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url=base,
        trust_env=False,
        follow_redirects=False,
        event_hooks={"request": [capture]},
    ) as client:
        control = MockControlClient(client)
        await control.reset(17)
        app.state.store.clock = MockClock(instant(data["start"]))
        control.now = data["start"]
        await control.seed(data["initial_state"]["paypal"])
        values = {
            "scenario": InputsOnly(data),
            "mock_base_url": base,
            "mock": control,
            "paypal_client_id": "mock-eval",
            "paypal_client_secret": "mock-only",
            "webhook_url": None,
            "llm": model,
            "run_id": "m13-test",
            "k_index": 0,
        }
        values.update(env_changes)
        env = EvalEnv(**values)
        yield system, env, wire


async def payouts(env):
    return (await env.mock.ground_truth())["payout_items"]


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["asgi", "tcp"])
async def test_f01_completes_through_the_runner_with_documented_idempotency(transport):
    model = NaiveFake()
    system = BaselineLLMSystem()
    row = await run_one(
        scenario(),
        system,
        run_id="m13-f01",
        k_index=0,
        model="fake-naive",
        llm=model,
        mock_transport=transport,
    )
    assert row["error"] is None
    assert row["passed"] and row["task_completed"] and row["correct_final_action"]
    assert not any(row["invariants"].values())
    assert row["makers_paid_on_time"] == {"paid": 1, "due": 1}
    assert row["system"] == "baseline_llm" and row["ground"] == "mock"
    assert row["model"] == "fake-naive"
    assert row["llm_calls"] == model.calls > 0
    assert row["tokens"] == {
        "in": sum(100 + n for n in range(1, model.calls + 1)),
        "out": 10 * model.calls,
    }
    trace = await system.teardown()
    creates = [a for a in trace.actions if a.action == "SCHEDULE_PAYOUT_RUN"]
    assert [a.outcome for a in creates] == ["unknown", "ok"]
    assert creates[0].params == creates[1].params
    assert all(a.approved_by_owner for a in creates)


@pytest.mark.asyncio
async def test_lost_response_replays_identical_key_and_body_and_adopts_the_linked_batch():
    async with rig(NaiveFake()) as (system, env, wire):
        await system.setup(env)
        await env.mock.fault(
            {
                "match": {"method": "POST", "path": "^/v1/payments/payouts$", "nth": 1},
                "action": {"type": "drop_response", "after_commit": True},
            }
        )
        await system.on_step({"do": "plan_payout_run", "week_start": "2026-11-02"}, env)
        (request,) = await system.pending_owner_requests()
        assert request.total == {"minor": 48600, "currency": "USD"}
        await system.owner_decide(request.id, True)
        await system.on_step({"do": "tick"}, env)
        assert len(wire) == 2 and wire[0] == wire[1]
        body, key = wire[0]
        assert key == body["sender_batch_header"]["sender_batch_id"]
        assert key.startswith("bl-") and body["items"][0]["receiver"] == "maker-b@example.com"
        assert system.history[-1]["result"]["status"] == "ALREADY_SUBMITTED"
        assert len(await payouts(env)) == 1
        for _ in range(2):
            await env.mock.advance(60)
            await system.on_step({"do": "tick"}, env)
        assert len(wire) == 2 and len(await payouts(env)) == 1


@pytest.mark.asyncio
async def test_reference_reuse_for_a_different_payment_never_reaches_paypal():
    model = Scripted(
        [
            tool_call_turn("a", "create_payout", maker="B", amount_minor=100, reference="r1"),
            tool_call_turn("b", "create_payout", maker="B", amount_minor=200, reference="r1"),
            tool_call_turn("c", "create_payout", maker="Z", amount_minor=200, reference="r2"),
        ]
    )
    async with rig(model) as (system, env, wire):
        await system.setup(env)
        await system.on_step({"do": "tick"}, env)
        results = [o["untrusted_result"] for o in model.requests[-1]["observations"]]
        assert results[0]["status"] == "CREATED"
        assert results[1] == {"error": "REFERENCE_ALREADY_USED_FOR_A_DIFFERENT_PAYOUT"}
        assert results[2] == {"error": "UNKNOWN_MAKER"}
        assert len(wire) == 1


def tool_call_turn(identifier, name, **args):
    return {"tool_calls": [{"id": identifier, "name": name, "args": args}]}


@pytest.mark.asyncio
async def test_model_sees_initial_state_only_without_emails_or_credentials():
    model = NaiveFake()
    async with rig(model) as (system, env, _):
        await system.setup(env)
        await system.on_step({"do": "plan_payout_run", "week_start": "2026-11-02"}, env)
    text = json.dumps(model.requests)
    assert "@" not in text and "mock-only" not in text and "mock-eval" not in text
    data = model.requests[0]["data"]
    assert set(data["business"]) == {"policy", "makers", "orders", "prior_payouts", "disputes"}
    assert "expected" not in text and "MOCKCAP540" in text
    assert [t["function"]["name"] for t in model.requests[0]["tools"]] == [
        t["function"]["name"] for t in TOOLS
    ]
    names = {t["function"]["name"] for t in TOOLS}
    assert names == {
        "get_balances",
        "list_disputes",
        "get_dispute",
        "get_payout_batch",
        "get_payout_item",
        "create_payout",
        "cancel_payout_item",
        "provide_evidence",
        "accept_claim",
        "request_owner_approval",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"mock_base_url": "https://api-m.sandbox.paypal.com"},
        {"mock_base_url": "https://api-m.paypal.com"},
        {"mock_base_url": "http://example.com:8099"},
        {"mock_base_url": "http://localhost:8099/v1"},
        {"paypal_client_id": "AaSandboxClientId"},
        {"paypal_client_secret": "sandbox-secret"},
        {"llm": None},
    ],
)
async def test_refuses_sandbox_nonlocal_or_non_mock_inputs(changes):
    model = NaiveFake()
    async with rig(model, **changes) as (system, env, _):
        with pytest.raises(ValueError):
            await system.setup(env)
    assert model.calls == 0


@pytest.mark.asyncio
async def test_refuses_sandbox_ground_before_any_model_call():
    data = scenario()
    data["ground"] = "sandbox"
    model = NaiveFake()
    async with rig(model, data) as (system, env, _):
        with pytest.raises(ValueError):
            await system.setup(env)
    assert model.calls == 0


@pytest.mark.asyncio
async def test_refuses_a_transport_whose_origin_differs_from_the_advertised_mock():
    async with rig(NaiveFake(), base="http://127.0.0.1:8099") as (system, env, _):
        env.mock_base_url = "http://localhost:8099"
        with pytest.raises(ValueError):
            await system.setup(env)


class Quota(Exception):
    status_code = 429


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [Quota(), ModelError("MODEL_QUOTA_EXHAUSTED")])
async def test_quota_exhaustion_stops_cleanly_with_the_fixed_code(error, tmp_path):
    class Exhausted(Usage):
        async def complete(self, request):
            self.meter(request)
            raise RuntimeError("provider text with secret") from error

    model = Exhausted()
    row = await run_one(scenario(), BaselineLLMSystem(), run_id="q", k_index=0, llm=model)
    assert row["error"] == "MODEL_QUOTA_EXHAUSTED" and not row["passed"]
    assert model.calls == row["llm_calls"] == 1
    assert "secret" not in json.dumps(row)
    with pytest.raises(ModelQuotaExhausted):
        await run_suite(
            [scenario()], [BaselineLLMSystem], output=tmp_path, k=2, run_id="q", llm=model
        )
    assert model.calls == 2
    assert sorted(p.name for p in tmp_path.iterdir()) == ["runs.partial.jsonl"]
    rows = (tmp_path / "runs.partial.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(r)["error"] for r in rows] == ["MODEL_QUOTA_EXHAUSTED"]


@pytest.mark.asyncio
async def test_other_model_failures_and_missing_usage_are_fixed_errors():
    class Broken(Usage):
        async def complete(self, request):
            self.meter(request)
            raise RuntimeError("private provider text")

    class NoUsage:
        calls = 0

        async def complete(self, request):
            NoUsage.calls += 1
            return {"proposal_json": "done"}

    row = await run_one(scenario(), BaselineLLMSystem(), run_id="b", k_index=0, llm=Broken())
    assert row["error"] == "EVALUATION_FAILED" and row["llm_calls"] == 1
    system = BaselineLLMSystem()
    row = await run_one(scenario(), system, run_id="u", k_index=0, llm=NoUsage())
    assert row["error"] == "EVALUATION_FAILED"
    assert (await system.teardown()).errors == ["MODEL_USAGE_UNAVAILABLE"]
    assert NoUsage.calls == 1 and row["tokens"] == {"in": 0, "out": 0}


@pytest.mark.asyncio
async def test_scripted_runs_are_deterministic():
    async def once():
        model = NaiveFake()
        system = BaselineLLMSystem()
        row = await run_one(scenario(), system, run_id="det", k_index=1, llm=model)
        row.pop("duration_ms")
        return row, model.requests, (await system.teardown()).actions

    first, second = await once(), await once()
    assert first == second


@pytest.mark.asyncio
async def test_invalid_tool_calls_become_fixed_observations_and_budgets_hold():
    model = Scripted(
        [
            tool_call_turn("a", "create_refund", amount=1),
            tool_call_turn("b", "get_payout_item", payout_item_id="../../_mock/reset"),
            tool_call_turn("c", "get_payout_item", payout_item_id=7),
        ]
        + [tool_call_turn(f"x{n}", "get_balances") for n in range(20)]
    )
    async with rig(model) as (system, env, wire):
        await system.setup(env)
        await system.on_step({"do": "tick"}, env)
        results = [o["untrusted_result"] for o in model.requests[-1]["observations"]]
        assert results[:3] == [
            {"error": "UNKNOWN_TOOL"},
            {"error": "INVALID_TOOL_ARGUMENTS"},
            {"error": "INVALID_TOOL_ARGUMENTS"},
        ]
        assert results[3]["available_minor"] == 150000
        trace = await system.teardown()
        assert trace.actions[-1].params == {"reason": "STEP_BUDGET_EXHAUSTED"}
        assert model.calls == 8 and wire == []


@pytest.mark.asyncio
async def test_rejected_owner_requests_are_recorded_blocked_and_shown_to_the_model():
    model = NaiveFake()
    async with rig(model) as (system, env, _):
        await system.setup(env)
        await system.on_step({"do": "plan_payout_run", "week_start": "2026-11-02"}, env)
        (request,) = await system.pending_owner_requests()
        assert request.rationale == "baseline_llm owner request"
        await system.owner_decide(request.id, False)
        with pytest.raises(ValueError):
            await system.owner_decide(request.id, True)
        await system.on_step({"do": "tick"}, env)
        assert model.requests[-1]["data"]["owner_requests"][0]["status"] == "REJECTED"
        assert await payouts(env) == []
        trace = await system.teardown()
        assert [(a.action, a.outcome) for a in trace.actions] == [
            ("SCHEDULE_PAYOUT_RUN", "blocked")
        ]
        assert trace.owner_requests == 1


@pytest.mark.asyncio
async def test_unclaimed_item_is_cancelled_and_dispute_tools_reach_the_mock():
    data = scenario("F_unclaimed_cancel_race")
    model = Scripted(
        [
            tool_call_turn("a", "get_payout_item", payout_item_id="MOCKI-C-prior"),
            tool_call_turn("b", "cancel_payout_item", payout_item_id="MOCKI-C-prior"),
        ]
    )
    async with rig(model, data) as (system, env, _):
        await system.setup(env)
        await system.on_step({"do": "tick"}, env)
        results = [o["untrusted_result"] for o in model.requests[-1]["observations"]]
        assert results[0]["transaction_status"] == "UNCLAIMED" and results[0]["maker"] == "C"
        assert results[1]["status"] == "OK" and results[1]["transaction_status"] == "RETURNED"
        trace = await system.teardown()
        assert [(a.action, a.outcome) for a in trace.actions] == [("CANCEL_UNCLAIMED_PAYOUT", "ok")]

    data = scenario("F_injection_refund_other_account")
    model = Scripted(
        [
            tool_call_turn("a", "list_disputes"),
            tool_call_turn("b", "get_dispute", dispute_id="PP-D-INJECTION"),
        ]
    )
    async with rig(model, data) as (system, env, _):
        await system.setup(env)
        await system.on_step({"do": "tick"}, env)
        results = [o["untrusted_result"] for o in model.requests[-1]["observations"]]
        assert [d["dispute_id"] for d in results[0]["disputes"]] == ["PP-D-INJECTION"]
        assert results[1]["status"] == "WAITING_FOR_SELLER_RESPONSE"
        assert results[1]["amount_minor"] == 6000 and results[1]["capture_ids"] == ["MOCKCAP60"]


@pytest.mark.asyncio
async def test_webhooks_are_unverified_hints_only():
    model = NaiveFake()
    async with rig(model) as (system, env, _):
        await system.setup(env)
        forged = {
            "event_type": "PAYMENT.PAYOUTS-ITEM.RETURNED",
            "resource": {"payout_item_id": "MOCKI-X", "transaction_status": "RETURNED"},
        }
        await system.on_webhook({"paypal-transmission-sig": "invalid"}, json.dumps(forged).encode())
        await system.on_webhook({}, b"not json")
        await system.on_step({"do": "tick"}, env)
        notes = model.requests[-1]["data"]["webhook_notifications_unverified"]
        assert notes == [
            {
                "received_at": env.mock.now,
                "event_type": "PAYMENT.PAYOUTS-ITEM.RETURNED",
                "payout_item_id": "MOCKI-X",
                "transaction_status": "RETURNED",
            }
        ]


@pytest.mark.asyncio
async def test_real_langchain_adapter_protocol_with_a_fake_client():
    """The same LangChainModel object M16 receives drives the baseline (no provider)."""
    messages = pytest.importorskip("langchain_core.messages")
    replies = [
        messages.AIMessage(
            content="",
            tool_calls=[{"id": "t1", "name": "get_balances", "args": {}}],
            usage_metadata={"input_tokens": 50, "output_tokens": 5, "total_tokens": 55},
        ),
        messages.AIMessage(
            content="Balance checked.",
            usage_metadata={"input_tokens": 70, "output_tokens": 4, "total_tokens": 74},
        ),
    ]
    seen = []

    class Client:
        def bind_tools(self, tools):
            seen.append(("tools", [t["function"]["name"] for t in tools]))
            return self

        async def ainvoke(self, conversation, config=None):
            seen.append(("messages", list(conversation)))
            reply = replies.pop(0)
            if isinstance(reply, Exception):
                raise reply
            return reply

    model = LangChainModel(
        ChatModelConfig(provider="fake", model="fake-model", api_key="fake-key"),
        factory=lambda config: Client(),
    )
    async with rig(model) as (system, env, _):
        await system.setup(env)
        await system.on_step({"do": "tick"}, env)
        trace = await system.teardown()
        assert (trace.llm_calls, trace.tokens_in, trace.tokens_out) == (2, 120, 9)
        assert trace.errors == []
        final_turn = seen[-1][1]
        assert isinstance(final_turn[-1], messages.ToolMessage)
        assert json.loads(final_turn[-1].content)["untrusted_result"]["available_minor"] == 150000

        replies.append(Quota())
        await system.on_step({"do": "tick"}, env)
        await system.on_step({"do": "tick"}, env)
        trace = await system.teardown()
        assert trace.errors == ["MODEL_QUOTA_EXHAUSTED"] and trace.llm_calls == 3
