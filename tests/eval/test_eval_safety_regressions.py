"""M18 fix: real facade refusals and long mock-clock OAuth expiry regressions."""

import asyncio
import json
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import test_countersign_system

from eval.runner import run_one
from eval.systems.countersign import CountersignSystem

SCENARIOS = Path(__file__).resolve().parents[2] / "eval/scenarios"
engine_database = test_countersign_system.engine_database
PLANNER_CASES = [
    "F_retry_settled_obligation",
    "F_double_spend_overlapping_weeks",
    "F_onhold_then_failed_replace_once",
    "F_webhook_duplicate_returned_resend_once",
    "F_double_spend_same_week_planned_twice",
    "F_stale_approval_dispute_opened",
    "F_stale_approval_partial_dispute",
    "F_insufficient_cash_run_exceeds_available",
]
CLOCK_CASES = [
    "demo_week",
    "F_blocked_new_payout_not_retried",
    "F_onhold_new_payout_not_replaced",
    "F_onhold_prior_released_later",
    "F_pending_past_run_stuck_item",
    "F_pending_prior_payout_not_replaced",
    "F_key_window_expired_lost_response",
    "F_key_window_expired_webhooks_dropped",
]


@pytest.mark.asyncio
@pytest.mark.parametrize("base", ["http://localhost:8099", "https://api-m.sandbox.paypal.com"])
async def test_engine_oauth_clock_override_is_local_mock_only(monkeypatch, base):
    from countersign import harness

    captured = {}
    now = datetime(2026, 11, 2, 9, tzinfo=UTC)

    class FakeHTTP:
        def __init__(self, settings, **kwargs):
            captured.update(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    monkeypatch.setattr(harness, "PayPalHTTP", FakeHTTP)
    settings = harness.EngineSettings(
        "postgresql://localhost/unused_eval",
        base,
        "fake-client",
        "fake-secret",
        "fake-webhook",
        lambda: now,
    )
    async with harness.Engine(settings, database=object(), executor=object(), verifier=object()):
        if base.startswith("http:"):
            before = captured["monotonic"]()
            now += timedelta(days=2)
            assert captured["monotonic"]() - before == 172800
        else:
            assert "monotonic" not in captured


@pytest.mark.integration
@pytest.mark.parametrize("name", PLANNER_CASES + CLOCK_CASES)
def test_previously_errored_rows_keep_real_failures_visible_without_mock_clock_failures(
    engine_database, name
):
    observed = Counter()

    class ObservedSystem(CountersignSystem):
        async def setup(self, env):
            async def response(reply):
                observed[reply.request.url.path, reply.status_code] += 1

            env.mock.client.event_hooks["response"].append(response)
            await super().setup(env)
            poll = self._engine._reconciler.poll_once

            async def stages():
                result = await poll()
                for stage in ("attempts", "disputes", "balances", "backfill"):
                    observed[stage, "errors"] += getattr(result, stage).errors
                return result

            self._engine._reconciler.poll_once = stages

    async def run():
        system = ObservedSystem(database_url=engine_database)
        scenario = json.loads((SCENARIOS / (name + ".json")).read_text())
        row = await run_one(scenario, system, run_id="m18-fix", k_index=0, model="none")
        assert row["error"] is None
        assert not system.trace.errors
        assert not any(row["invariants"].values())
        assert all(
            not observed[stage, "errors"]
            for stage in ("attempts", "disputes", "balances", "backfill")
        )
        assert not any(count for (path, status), count in observed.items() if status == 401)
        if name in CLOCK_CASES:
            assert observed["/v1/oauth2/token", 200] >= 2
        # Completion is deliberately left to the unchanged ground-truth grader.
        # Model-disabled missing agent actions must not be converted into passes.
        assert row["llm_calls"] == 0

    with asyncio.Runner(
        loop_factory=asyncio.SelectorEventLoop
        if sys.platform == "win32"
        else asyncio.new_event_loop
    ) as runner:
        runner.run(run())


@pytest.mark.integration
@pytest.mark.parametrize(
    "name",
    [
        "F_triage_snad_broken_mug_refund",
        "F_injection_new_payee_unclaimed_resend",
        "F_deadline_close_evidence_due_soon",
    ],
)
def test_missing_model_actions_remain_failed_outcomes_not_hidden(engine_database, name):
    async def run():
        scenario = json.loads((SCENARIOS / (name + ".json")).read_text())
        row = await run_one(
            scenario,
            CountersignSystem(database_url=engine_database),
            run_id="m18-model-disabled",
            k_index=0,
            model="none",
        )
        assert row["error"] is None
        assert row["passed"] is False
        assert row["llm_calls"] == 0

    with asyncio.Runner(
        loop_factory=asyncio.SelectorEventLoop
        if sys.platform == "win32"
        else asyncio.new_event_loop
    ) as runner:
        runner.run(run())


@pytest.mark.integration
@pytest.mark.parametrize(
    "name",
    [
        "F_key_window_expired_lost_response",
        "F_key_window_expired_webhooks_dropped",
        "F_onhold_then_failed_replace_once",
    ],
)
def test_m21_observed_schedule_and_unfixed_failed_replacement(engine_database, name):
    async def run():
        scenario = json.loads((SCENARIOS / (name + ".json")).read_text())
        system = CountersignSystem(database_url=engine_database)
        row = await run_one(scenario, system, run_id="m21", k_index=0, model="none")
        assert row["error"] is None and not any(row["invariants"].values())
        assert row["correct_final_action"] is True
        if name.startswith("F_key_window"):
            assert row["passed"] is True
            assert row["makers_paid_on_time"] == {"paid": 1, "due": 1}
            actions = [a for a in system.trace.actions if a.action == "SCHEDULE_PAYOUT_RUN"]
            assert len(actions) == 1 and actions[0].outcome == "ok"
        else:
            # Core only plans RETURNED replacements. Preserve the honest failure.
            assert row["passed"] is False and row["task_completed"] is False
            assert row["makers_paid_on_time"] == {"paid": 0, "due": 1}

    with asyncio.Runner(
        loop_factory=asyncio.SelectorEventLoop
        if sys.platform == "win32"
        else asyncio.new_event_loop
    ) as runner:
        runner.run(run())
