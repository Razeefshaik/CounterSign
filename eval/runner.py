"""M11 mock-only timeline runner.

Smoke check (no PayPal credentials or services required):
    python -m eval.runner --dummy --k 2 --output <temporary-folder>

Each repetition owns a fresh in-process mock. Supply System factories to
run_suite() for actual evaluation. The dummy does nothing; it is not a baseline.
Scenario time, owner delays, deliveries and deadlines use the mock clock.

mock_transport="asgi" (default) serves the mock through httpx's in-process
transport. mock_transport="tcp" serves the same in-process app on an ephemeral
127.0.0.1 port (eval/tcp_mock.py), so a separate process such as CounterSign's
genuine toolkit child can reach mock_base_url. Configured-model runs use "tcp".
A run whose model reports MODEL_QUOTA_EXHAUSTED is labelled with that fixed code,
and run_suite stops: it writes runs.partial.jsonl only, never a summary.
"""

import argparse
import asyncio
import json
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import perf_counter_ns
from urllib.parse import quote

import httpx

from eval.metrics import INVARIANTS, grade, instant
from eval.owner import OwnerPolicies
from eval.report import write_results
from eval.systems.base import EvalEnv, MockControlClient, System, SystemTrace
from eval.validate import seconds, validate
from mock_paypal.app import create_app
from mock_paypal.clock import MockClock, timestamp

MOCK_TRANSPORTS = ("asgi", "tcp")
QUOTA_EXHAUSTED = "MODEL_QUOTA_EXHAUSTED"
ASGI_BASE_URL = "http://localhost:8099"


class ModelQuotaExhausted(RuntimeError):
    """Fixed stop code: the model provider refused further calls (HTTP 429)."""

    def __init__(self):
        super().__init__(QUOTA_EXHAUSTED)


@asynccontextmanager
async def mock_session(delivery, mock_transport="asgi"):
    """Yield (app, client) for one fresh control-enabled mock.

    The app always stays in this process: the runner replaces its clock and
    receives its webhook deliveries directly. Only the HTTP path differs.
    """
    if mock_transport not in MOCK_TRANSPORTS:
        raise ValueError("Unknown mock transport")

    def factory(base_url):
        return create_app(control_enabled=True, webhook_delivery=delivery, mock_base_url=base_url)

    if mock_transport == "asgi":
        app = factory(ASGI_BASE_URL)
        async with httpx.AsyncClient(
            base_url=ASGI_BASE_URL,
            transport=httpx.ASGITransport(app=app),
            trust_env=False,
            follow_redirects=False,
        ) as client:
            yield app, client
        return
    from eval.tcp_mock import serve_loopback

    async with (
        serve_loopback(factory) as (app, base_url),
        httpx.AsyncClient(
            base_url=base_url,
            trust_env=False,
            follow_redirects=False,
            # Webhook deliveries run inside a clock advance and may call the mock again.
            timeout=httpx.Timeout(60),
        ) as client,
    ):
        yield app, client


class DummySystem:
    """Acceptance-test system, labelled dummy so it cannot masquerade as a baseline."""

    name = "dummy"

    async def setup(self, env):
        pass

    async def on_step(self, step, env):
        pass

    async def on_webhook(self, headers, raw_body):
        pass

    async def pending_owner_requests(self):
        return []

    async def owner_decide(self, request_id, approve):
        pass

    async def teardown(self):
        return SystemTrace([], 0, [], 0, 0, 0)


async def run_one(
    scenario: dict,
    system: System,
    *,
    run_id: str,
    k_index: int,
    model: str = "none",
    llm=None,
    mock_transport: str = "asgi",
) -> dict:
    validate(scenario)
    scenario = deepcopy(scenario)
    if scenario["ground"] != "mock":
        raise ValueError("The M11 runner accepts mock scenarios only")
    if system.name not in {"dummy", "baseline_llm", "rules_only", "countersign"}:
        raise ValueError("Unknown evaluation system")
    if mock_transport not in MOCK_TRANSPORTS:
        raise ValueError("Unknown mock transport")
    start = instant(scenario["start"])
    deadline = start + timedelta(seconds=seconds(scenario["expected"]["deadline"]))
    owner = OwnerPolicies(scenario["expected"])
    trace = SystemTrace([], 0, [], 0, 0, 0)
    failed = False
    deadline_truth = None
    started = perf_counter_ns()
    control = None

    async def delivery(url, headers, raw_body):
        control.now = timestamp(app.state.store.clock.now)
        await system.on_webhook(deepcopy(headers), bytes(raw_body))
        return 200

    async with mock_session(delivery, mock_transport) as (app, client):
        control = MockControlClient(client)
        owner.observe_truth = control.ground_truth
        await control.reset(scenario["seed"])
        # Reset clears timers. Initialise any RFC3339 scenario epoch before seeding.
        app.state.store.clock = MockClock(start)
        control.now = timestamp(start)
        await control.seed(scenario["initial_state"]["paypal"])
        token = await client.post(
            "/v1/oauth2/token",
            auth=("mock-eval", "mock-only"),
            data={"grant_type": "client_credentials"},
        )
        token.raise_for_status()
        client.headers["Authorization"] = "Bearer " + token.json()["access_token"]
        env = EvalEnv(
            scenario,
            str(client.base_url).rstrip("/"),
            control,
            "mock-eval",
            "mock-only",
            "http://localhost:8098/webhooks/paypal",
            llm,
            run_id,
            k_index,
        )
        registered = await client.post(
            "/v1/notifications/webhooks",
            json={
                "url": env.webhook_url,
                "event_types": [{"name": "*"}],
            },
        )
        registered.raise_for_status()

        async def now():
            return instant((await control.clock())["now"])

        async def move(target):
            nonlocal deadline_truth
            current = await now()
            while current < target:
                if current >= deadline and deadline_truth is None:
                    deadline_truth = await control.ground_truth()
                stops = [target]
                if current < deadline:
                    stops.append(deadline)
                due = owner.next_due(current)
                if due is not None:
                    stops.append(due)
                stop = min(stops)
                await control.advance(int((stop - current).total_seconds()))
                current = await now()
                await owner.apply(system, current)

        try:
            await system.setup(env)
            for step in scenario["timeline"]:
                await move(max(await now(), start + timedelta(seconds=seconds(step["at"]))))
                kind = step["do"]
                if kind == "tick":
                    for _ in range(step.get("repeat", 1)):
                        await system.on_step(deepcopy(step), env)
                        await owner.apply(system, await now())
                        await move(await now() + timedelta(seconds=step.get("advance_seconds", 0)))
                elif kind == "plan_payout_run":
                    await system.on_step(deepcopy(step), env)
                elif kind == "advance":
                    await move(await now() + timedelta(seconds=step["seconds"]))
                elif kind == "fault":
                    await control.fault(step["fault"])
                elif kind == "webhook_fault":
                    await control.webhook_fault(step["fault"])
                elif kind == "mock":
                    await control.request(step["method"], step["path"], step.get("body"))
                elif kind == "owner":
                    owner.configure(step["policy"], step.get("seconds", 0))
                elif kind == "buyer_message":
                    key = step["dispute"]
                    matches = [
                        d for d in scenario["initial_state"]["disputes"] if d.get("key") == key
                    ]
                    if matches:
                        key = matches[0]["paypal_dispute_id"]
                    await control.request(
                        "POST",
                        f"/_mock/disputes/{quote(key, safe='')}/buyer-message",
                        {"content": step["content"]},
                    )
                await owner.apply(system, await now())
            await move(max(await now(), deadline))
        except Exception:  # noqa: BLE001 -- isolate arbitrary System plugins; never publish raw errors
            failed = True
        finally:
            if deadline_truth is None:
                deadline_truth = await control.ground_truth()
            try:
                trace = await system.teardown()
                if not isinstance(trace, SystemTrace) or any(
                    type(value) is not int or value < 0
                    for value in (
                        trace.owner_requests,
                        trace.llm_calls,
                        trace.tokens_in,
                        trace.tokens_out,
                    )
                ):
                    trace = SystemTrace([], 0, [], 0, 0, 0)
                    failed = True
            except Exception:  # noqa: BLE001 -- teardown is an arbitrary plugin boundary too
                failed = True
        truth = await control.ground_truth()
        # The runner records real policy decisions; claimed approval booleans alone do not grade money.
        try:
            metrics = grade(
                scenario,
                truth,
                deadline_truth,
                trace,
                owner.decisions,
                deadline=deadline,
                unnecessary=owner.unnecessary,
            )
        except Exception:  # noqa: BLE001 -- invalid plugin traces must produce a failed, redacted row
            failed = True
            metrics = {
                "invariants": dict.fromkeys(INVARIANTS, 0),
                "passed": False,
                "correct_final_action": False,
                "task_completed": False,
                "makers_paid_on_time": {
                    "paid": 0,
                    "due": len({p["maker"] for p in scenario["expected"]["payouts"]}),
                },
                "owner_escalations": len(owner.seen),
                "unnecessary_escalations": owner.unnecessary,
            }
        failed = failed or bool(trace.errors)
        if failed:
            metrics["passed"] = False
        error = None
        if QUOTA_EXHAUSTED in trace.errors:
            error = QUOTA_EXHAUSTED
        elif failed:
            error = "EVALUATION_FAILED"
        metrics["owner_escalations"] = max(metrics["owner_escalations"], len(owner.seen))
        return {
            "run_id": run_id,
            "system": system.name,
            "scenario_id": scenario["id"],
            "fault_class": scenario["fault_class"],
            "provenance": scenario["provenance"],
            "ground": "mock",
            "k": k_index,
            "seed": scenario["seed"],
            "model": model,
            **metrics,
            "duration_ms": (perf_counter_ns() - started) // 1_000_000,
            "llm_calls": trace.llm_calls,
            "tokens": {"in": trace.tokens_in, "out": trace.tokens_out},
            "error": error,
        }


async def run_suite(
    scenarios: list[dict],
    factories: list,
    *,
    output: str | Path,
    k: int = 5,
    run_id: str | None = None,
    model="none",
    llm=None,
    mock_transport: str = "asgi",
) -> list[dict]:
    if type(k) is not int or k < 1 or not scenarios or not factories:
        raise ValueError("Scenarios, systems and positive k are required")
    if mock_transport not in MOCK_TRANSPORTS:
        raise ValueError("Unknown mock transport")
    if len({s["id"] for s in scenarios}) != len(scenarios):
        raise ValueError("Duplicate scenario IDs")
    # Preflight all scenarios before a system can run or any files are written.
    for scenario in scenarios:
        validate(scenario)
        if scenario["ground"] != "mock":
            raise ValueError("Sandbox scenarios require a separate integration runner")
    run_id = run_id or datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%SZ")
    rows = []
    for factory in factories:
        for scenario in scenarios:
            for index in range(k):
                row = await run_one(
                    scenario,
                    factory(),
                    run_id=run_id,
                    k_index=index,
                    model=model,
                    llm=llm,
                    mock_transport=mock_transport,
                )
                rows.append(row)
                if row["error"] == QUOTA_EXHAUSTED:
                    # Later runs would only fail on quota too; an incomplete
                    # comparison gets no summary, report or pass^k.
                    folder = Path(output)
                    folder.mkdir(parents=True, exist_ok=True)
                    (folder / "runs.partial.jsonl").write_text(
                        "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows),
                        encoding="utf-8",
                    )
                    raise ModelQuotaExhausted()
    write_results(rows, output, k)
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*")
    parser.add_argument("--dummy", action="store_true", required=True)
    parser.add_argument("--k", type=int, default=2)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    paths = [Path(p) for p in args.paths] or sorted(
        Path(__file__).with_name("scenarios").glob("*.json")
    )
    try:
        scenarios = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
        rows = asyncio.run(
            run_suite(scenarios, [DummySystem], output=args.output, k=args.k, model="dummy-no-op")
        )
    except Exception:  # noqa: BLE001 -- CLI diagnostics must not echo plugin credentials or inputs
        print("Evaluation failed; no raw input or credentials are printed.")
        return 1
    print(
        f"Wrote {len(rows)} dummy smoke runs; {sum(r['passed'] for r in rows)} passed. This is not a baseline benchmark."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
