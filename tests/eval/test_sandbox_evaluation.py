"""C26 never uses sandbox credentials or a model provider in tests."""

import asyncio
import copy
import json
import os
import sys
from types import SimpleNamespace

import pytest

from eval.sandbox import (
    CASES,
    integration_rows,
    load_case_catalog,
    scenario_templates,
    write_integration_results,
)
from scripts.sandbox_eval import (
    database_guard,
    engine_seed,
    mock_resources,
    resource_manifest,
    run_mock,
    target_guard,
)
from scripts.slice_e2e import SliceError


@pytest.mark.parametrize(
    "target,dry_run", [("mock", False), ("sandbox", True), ("production", False)]
)
def test_target_requires_explicit_safe_mode(target, dry_run):
    with pytest.raises(SliceError):
        target_guard(target, dry_run)


@pytest.mark.parametrize(
    "mode,base",
    [
        ("demo", "https://api-m.sandbox.paypal.com"),
        ("live", "https://api-m.paypal.com"),
        ("live", "http://127.0.0.1:8099"),
    ],
)
def test_sandbox_guard_rejects_production_and_mock(mode, base):
    settings = SimpleNamespace(
        mode=mode,
        database_url="postgresql://localhost/c26_eval",
        paypal=SimpleNamespace(base_url=base, is_mock="localhost" in base),
    )
    with pytest.raises(SliceError):
        target_guard("sandbox", False, settings)


def test_sandbox_guard_accepts_only_dedicated_database():
    settings = SimpleNamespace(
        mode="live",
        database_url="postgresql://localhost/c26_eval",
        paypal=SimpleNamespace(base_url="https://api-m.sandbox.paypal.com", is_mock=False),
    )
    target_guard("sandbox", False, settings)
    for url in [
        "postgresql://localhost/production",
        "postgresql://localhost/c26_eval?options=-csearch_path=public",
    ]:
        with pytest.raises(SliceError):
            database_guard(url)


def test_manifest_and_seed_have_small_integer_amounts():
    resources = mock_resources()
    resources["dispute_id"] = "PP-D-C26"
    seed = engine_seed(resources)
    assert len(seed["orders"]) == 4
    assert all(order["amount"] == {"minor": 200, "currency": "USD"} for order in seed["orders"])
    assert seed["disputes"] == [{"paypal_dispute_id": "PP-D-C26", "order": "order-D"}]
    resources["emails"]["U"] = resources["emails"]["R"]
    with pytest.raises(SliceError):
        resource_manifest(resources)


def test_integration_reports_do_not_fabricate_invariant_measurements(tmp_path):
    store = SimpleNamespace(
        directory=tmp_path,
        data={
            "item_ids": ["ITEM1"],
            "dispute_id": "PP-D-C26",
            "dispute_confirmed": True,
            "cancel_confirmed": True,
            "replay_confirmed": True,
            "settlement_block_proposal_id": "PROPOSAL1",
            "observed_items": {key: {} for key in "RUL"},
        },
    )
    for ground in ["mock", "sandbox"]:
        rows = integration_rows(store, target=ground)
        assert {row["scenario_id"] for row in rows} == set(CASES)
        assert all(row["ground"] == ground and row["system"] == "countersign" for row in rows)
        assert all(set(row["invariants"].values()) == {None} for row in rows)
        write_integration_results(rows, tmp_path / ground)
        summary = json.loads((tmp_path / ground / "summary.json").read_text())
        assert summary["invariants"] == "unavailable"
        text = (tmp_path / ground / "report.md").read_text()
        assert "null counters do not certify safety" in text
        assert ("not sandbox verification" in text) == (ground == "mock")


def test_incomplete_checkpoint_cannot_claim_five_passes(tmp_path):
    store = SimpleNamespace(directory=tmp_path, data={})
    with pytest.raises(SliceError, match="C26_INCOMPLETE_OBSERVATIONS"):
        integration_rows(store, target="sandbox")


def test_catalog_matches_checked_cases_and_rejects_unimplemented_edits(tmp_path):
    assert {doc["id"] for doc in load_case_catalog()} == set(CASES)
    assert all(doc["ground"] == "sandbox" for doc in load_case_catalog())
    for doc in scenario_templates():
        (tmp_path / (doc["id"] + ".json")).write_text(json.dumps(doc))
    assert load_case_catalog(tmp_path) == scenario_templates()
    path = tmp_path / (CASES[0] + ".json")
    changed = json.loads(path.read_text())
    changed["expected"]["payouts"][0]["amount"]["minor"] = 181
    path.write_text(json.dumps(changed))
    with pytest.raises(SliceError, match="C26_SCENARIO_SPECIFICATION_CHANGED"):
        load_case_catalog(tmp_path)


@pytest.mark.asyncio
async def test_m11_sandbox_entrypoint_delegates_without_weakening_mock_guards(monkeypatch):
    from eval import runner, sandbox

    seen = []

    async def fake(*args, **kwargs):
        seen.append((args, kwargs))
        return ["observed"]

    monkeypatch.setattr(sandbox, "run_integration", fake)
    assert await runner.run_sandbox_integration("engine", target="sandbox") == ["observed"]
    assert seen == [(("engine",), {"target": "sandbox"})]
    with pytest.raises(ValueError, match="mock scenarios only"):
        await runner.run_one(
            load_case_catalog()[0], runner.DummySystem(), run_id="refused", k_index=0
        )


@pytest.mark.parametrize("cli", ["dummy", "rules_only"])
def test_default_mock_clis_exclude_sandbox_catalog(cli, tmp_path, monkeypatch):
    from eval import runner
    from eval.systems import rules_only

    seen = []

    async def suite(scenarios, factories, **kwargs):
        seen.extend(scenarios)
        return [{"passed": True}]

    monkeypatch.setattr(runner, "run_suite", suite)
    if cli == "dummy":
        assert runner.main(["--dummy", "--k", "1", "--output", str(tmp_path)]) == 0
    else:
        monkeypatch.setattr(sys, "argv", ["rules_only", "--k", "1", "--output", str(tmp_path)])
        rules_only.main()
    assert len(seen) >= 48
    assert all(doc["ground"] == "mock" for doc in seen)
    assert not set(CASES) & {doc["id"] for doc in seen}


def test_cli_refuses_unsafe_mode_before_settings_or_network(tmp_path, monkeypatch, capsys):
    from scripts import sandbox_eval

    def forbidden():
        pytest.fail("Unsafe target must fail before settings or network")

    monkeypatch.setattr(sandbox_eval, "load_settings", forbidden)
    assert sandbox_eval.main(["--target", "sandbox", "--dry-run", "--output", str(tmp_path)]) == 1
    assert not list(tmp_path.iterdir())
    assert "C26 stopped" in capsys.readouterr().out


def test_interrupted_owner_approval_resumes_without_another_write(tmp_path, monkeypatch):
    from eval import sandbox as integration
    from scripts.sandbox_checks.redact import Redactor
    from scripts.slice_e2e import Checkpoint

    resources = mock_resources()
    resources["dispute_id"] = "PP-D-C26"
    model = SimpleNamespace(plans={})
    model.arm = lambda incident, action, params: model.plans.update(
        {str(incident): (action, params)}
    )
    store = Checkpoint(
        tmp_path / "resume",
        target="sandbox",
        identity={"test": "resume"},
        redactor=Redactor(),
        output=lambda _: None,
    )
    store.checkpoint(week_start="2026-11-02", dispute_id=resources["dispute_id"])

    class Interrupted(RuntimeError):
        pass

    class Engine:
        database = None
        settings = SimpleNamespace(clock=lambda: None)
        approvals = 0

        def __init__(self):
            self.doc = {
                "runs": [],
                "proposals": [],
                "operations": [],
                "makers": [
                    {
                        "id": k,
                        "display_name": "C26 " + k,
                        "reserved": {"minor": 180 if k == "D" else 0},
                        "settled_last_30d": {"minor": 0},
                    }
                    for k in "RULD"
                ],
            }
            self.status = {k: "UNCLAIMED" if k == "U" else "SUCCESS" for k in "RUL"}

        async def snapshot(self):
            return copy.deepcopy(self.doc)

        async def plan_payout_run(self, week):
            self.doc["runs"].append({"id": "RUN1"})
            self.doc["proposals"].append(
                {
                    "id": "PLAN1",
                    "incident_id": "PLAN-INCIDENT",
                    "action": "SCHEDULE_PAYOUT_RUN",
                    "state": "NEEDS_OWNER",
                    "approval_request": {"payload_hash": "sha256:exact"},
                    "params": {
                        "run_id": "RUN1",
                        "lines": [
                            {
                                "line_id": "LINE" + k,
                                "maker_id": k,
                                "amount": {"minor": 180, "currency": "USD"},
                            }
                            for k in "RUL"
                        ],
                    },
                }
            )
            return "RUN1"

        async def pending_approvals(self):
            return [p for p in self.doc["proposals"] if p["state"] == "NEEDS_OWNER"]

        async def tick(self):
            plan = next((p for p in self.doc["proposals"] if p["id"] == "PLAN1"), None)
            if plan and plan["state"] == "APPROVED":
                plan["state"] = "EXECUTED"
                self.doc["operations"] += [{"id": "OP" + k} for k in "RUL"]
                for maker in self.doc["makers"]:
                    if maker["id"] in "RL":
                        maker["settled_last_30d"]["minor"] = 180
                store.checkpoint(
                    item_ids=["ITEM" + k for k in "RUL"],
                    lost_response_discarded=True,
                    duplicate_links=["https://api.sandbox.paypal.com/v1/payments/payouts/BATCHR"],
                )
            for incident, (action, _) in model.plans.items():
                if any(p.get("incident_id") == incident for p in self.doc["proposals"]):
                    continue
                cancel = action == "CANCEL_UNCLAIMED_PAYOUT"
                if cancel:
                    self.status["U"] = "RETURNED"
                    self.doc["operations"].append({"id": "OP-CANCEL"})
                self.doc["proposals"].append(
                    {
                        "id": "P-" + incident,
                        "incident_id": incident,
                        "verdict": "ALLOW" if cancel else "BLOCK",
                        "state": "EXECUTED" if cancel else "BLOCKED",
                        "rule_results": []
                        if cancel
                        else [{"rule": "SETTLEMENT", "outcome": "BLOCK"}],
                    }
                )

    engine = Engine()

    class HTTP:
        async def request(self, method, path):
            assert method == "GET"
            if "/captures/" in path:
                body = {"status": "PENDING"}
            else:
                key = path[-1]
                item = {
                    "payout_item_id": "ITEM" + key,
                    "payout_batch_id": "BATCH" + key,
                    "transaction_status": engine.status[key],
                    "payout_item": {
                        "amount": {"value": "1.80", "currency": "USD"},
                        "receiver": resources["emails"][key],
                    },
                }
                body = (
                    {"batch_header": {"payout_batch_id": "BATCH" + key}, "items": [item]}
                    if "/payouts/" in path
                    else item
                )
            return SimpleNamespace(status_code=200, body=body)

    async def open_incident(kind, subject):
        return {"id": subject["id"]}

    monkeypatch.setattr(
        integration, "IncidentService", lambda *a, **kw: SimpleNamespace(open=open_incident)
    )

    async def advance():
        pass

    async def approve(proposal_id, payload_hash):
        assert proposal_id == "PLAN1" and payload_hash == "sha256:exact"
        engine.approvals += 1
        engine.doc["proposals"][0]["state"] = "APPROVED"
        raise Interrupted("Process stopped after durable approval, before file checkpoint")

    async def run():
        with pytest.raises(Interrupted):
            await integration.run_integration(
                engine,
                HTTP(),
                store,
                model,
                resources,
                target="sandbox",
                advance=advance,
                approve=approve,
            )
        assert store.state["steps"]["owner_approved"] == "IN_PROGRESS"
        rows = await integration.run_integration(
            engine,
            HTTP(),
            store,
            model,
            resources,
            target="sandbox",
            advance=advance,
            approve=approve,
        )
        assert len(rows) == 5
        assert engine.approvals == 1 and len(engine.doc["operations"]) == 4
        assert rows[2]["replay_resources"] == [
            {"payout_batch_id": "BATCHR", "payout_item_id": "ITEMR"}
        ]
        # Completed checkpoints must still re-GET resources before claiming success.
        engine.status["R"] = "RETURNED"
        with pytest.raises(SliceError, match="C26_FINAL_ITEM_CHANGED"):
            await integration.run_integration(
                engine,
                HTTP(),
                store,
                model,
                resources,
                target="sandbox",
                advance=advance,
                approve=approve,
            )
        assert len(engine.doc["operations"]) == 4

    try:
        asyncio.run(run())
    finally:
        store.close()


@pytest.mark.integration
def test_five_cases_end_to_end_on_real_engine_and_mock(tmp_path):
    from eval.evaluate import disposable_database

    base = os.environ.get("COUNTERSIGN_TEST_DATABASE_URL")
    if not base:
        pytest.skip("COUNTERSIGN_TEST_DATABASE_URL is unset")
    with (
        disposable_database(base) as database_url,
        asyncio.Runner(
            loop_factory=asyncio.SelectorEventLoop
            if sys.platform == "win32"
            else asyncio.new_event_loop
        ) as runner,
    ):
        rows = runner.run(run_mock(database_url, tmp_path / "mock", poll_limit=120))
    assert len(rows) == 5 and all(row["passed"] for row in rows)
    assert all(row["ground"] == "mock" for row in rows)
    state = json.loads((tmp_path / "mock/state.json").read_text())
    assert state["data"]["cancel_confirmed"]
    assert state["data"]["replay_confirmed"]
    assert state["data"]["settlement_block_proposal_id"]
    assert len(state["data"]["item_ids"]) == 3
    public = (tmp_path / "mock/run.jsonl").read_text()
    assert "@example.test" not in public
    assert "C26_SETTLED_RETRY_BLOCKED" in public
