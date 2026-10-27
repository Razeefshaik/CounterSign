"""M14 fault library: coverage, honest provenance, and end-to-end runs on the mock.

rules_only runs every scenario; it is not required to pass them (its scores are
recorded in docs/walkthrough/m14-fault-library.md). The countersign smoke test
uses the real Engine on PostgreSQL with the model disabled.
"""

import asyncio
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path
from typing import get_args
from uuid import uuid4

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from psycopg import sql
from psycopg.conninfo import make_conninfo
from pydantic import ValidationError
from sqlalchemy import create_engine

from eval.metrics import INVARIANTS
from eval.runner import run_one
from eval.systems.rules_only import RulesOnlySystem
from eval.validate import FAULT_CLASSES, synthesised_reasons, validate

ROOT = Path(__file__).resolve().parents[2]
FOLDER = ROOT / "eval/scenarios"
FILES = sorted(FOLDER.glob("*.json"))
LIBRARY = {
    path.stem: doc
    for path in FILES
    if (doc := json.loads(path.read_text(encoding="utf-8")))["ground"] == "mock"
}
# Delay and timeout faults wait on the mock clock, which the in-process runner
# advances only between steps; a system awaiting such a request would never return.
BLOCKING_FAULTS = {"timeout", "delay"}


def test_every_fault_class_has_two_scenarios_and_triage_has_three():
    counts = Counter(doc["fault_class"] for doc in LIBRARY.values())
    assert set(counts) == set(get_args(FAULT_CLASSES))
    assert all(count >= 2 for count in counts.values()), counts
    assert counts["UNSTRUCTURED_DISPUTE_TRIAGE"] >= 3


def test_walkthrough_lists_every_mock_scenario_once():
    walkthrough = (ROOT / "docs/walkthrough/m14-fault-library.md").read_text(encoding="utf-8")
    names = re.findall(r"^\| `([^`]+)`", walkthrough, flags=re.MULTILINE)
    assert Counter(names) == Counter(LIBRARY.keys())


@pytest.mark.parametrize("name", sorted(LIBRARY))
def test_provenance_is_honest_and_labelled_in_the_task(name):
    doc = LIBRARY[name]
    reasons = synthesised_reasons(validate(doc))
    text = doc["task"]["description"]
    if doc["provenance"] == "recorded":
        assert not reasons
    if doc["provenance"] != "recorded":
        assert "Synthesised (F13):" in text
    if doc["provenance"] != "synthesised":
        assert "Recorded:" in text


def mutated(change):
    data = json.loads(json.dumps(LIBRARY["clean_run_three_makers"]))
    assert data["provenance"] == "recorded"
    change(data)
    return data


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d["timeline"].insert(
            0,
            {
                "at": "+0h",
                "do": "mock",
                "method": "POST",
                "path": "/_mock/payout-outcomes",
                "body": {"receiver": "maker-b@example.com", "to": "BLOCKED"},
            },
        ),
        lambda d: d["timeline"].insert(
            0,
            {
                "at": "+0h",
                "do": "webhook_fault",
                "fault": {"match": {"event_type": "*"}, "action": {"type": "drop"}},
            },
        ),
        lambda d: d["timeline"].insert(
            0,
            {
                "at": "+0h",
                "do": "fault",
                "fault": {
                    "match": {"method": "POST", "path": "^/v1/payments/payouts$"},
                    "action": {"type": "drop_response", "after_commit": True},
                },
            },
        ),
        lambda d: d["initial_state"]["paypal"]["payout_items"].append(
            {
                "payout_item_id": "MOCKI-held",
                "receiver": "maker-b@example.com",
                "amount": "1.00",
                "status": "ONHOLD",
            }
        ),
    ],
    ids=["outcome-control", "webhook-fault", "transport-fault", "onhold-seed"],
)
def test_recorded_provenance_rejects_synthesised_behaviour(change):
    data = mutated(change)
    with pytest.raises(ValidationError, match="Recorded provenance"):
        validate(data)
    data["provenance"] = "mixed"
    assert synthesised_reasons(validate(data))


def test_recorded_provenance_allows_replayed_recorded_test_values():
    data = mutated(
        lambda d: d["timeline"].insert(
            0,
            {
                "at": "+0h",
                "do": "fault",
                "fault": {
                    "match": {"method": "POST", "path": "^/v1/payments/payouts$"},
                    "action": {"type": "test_value", "value": "ERRPYO036"},
                },
            },
        )
    )
    assert validate(data).provenance == "recorded"


def test_seeded_failed_item_is_not_a_recorded_d7_rejection():
    data = mutated(
        lambda d: d["initial_state"]["paypal"]["payout_items"].append(
            {
                "payout_item_id": "MOCKI-failed",
                "receiver": "maker-b@example.com",
                "amount": "1.00",
                "status": "FAILED",
            }
        )
    )
    with pytest.raises(ValidationError, match="Recorded provenance"):
        validate(data)


@pytest.mark.parametrize("name", sorted(LIBRARY))
def test_library_is_runnable_in_process_and_internally_consistent(name):
    doc = LIBRARY[name]
    expected, initial = doc["expected"], doc["initial_state"]
    assert not any(
        step["do"] == "fault" and step["fault"]["action"]["type"] in BLOCKING_FAULTS
        for step in doc["timeline"]
    )
    paid = {entry["maker"] for entry in expected["payouts"]}
    assert not paid & set(expected["no_payout_to"])
    verified = {m["key"] for m in initial["makers"] if m["verified"]}
    assert paid <= verified
    assert all(entry["total"]["minor"] > 0 for entry in expected["owner_should_approve"])
    seeded = {d["dispute_id"] for d in initial["paypal"]["disputes"]}
    assert {refund["dispute_id"] for refund in expected["refunds"]} <= seeded
    emails = {m["payout_email"] for m in initial["makers"]}
    for step in doc["timeline"]:
        if step["do"] == "mock" and step["path"] == "/_mock/payout-outcomes":
            assert step["body"]["receiver"] in emails


@pytest.mark.asyncio
@pytest.mark.parametrize("name", sorted(LIBRARY))
async def test_rules_only_completes_every_scenario_and_metrics_compute(name):
    doc = LIBRARY[name]
    row = await run_one(doc, RulesOnlySystem(), run_id="m14-library", k_index=0, model="none")
    assert row["error"] is None
    assert row["scenario_id"] == name and row["fault_class"] == doc["fault_class"]
    assert row["provenance"] == doc["provenance"] and row["ground"] == "mock"
    assert set(row["invariants"]) == set(INVARIANTS)
    assert all(type(value) is int and value >= 0 for value in row["invariants"].values())
    for key in ("passed", "correct_final_action", "task_completed"):
        assert type(row[key]) is bool
    due = {entry["maker"] for entry in doc["expected"]["payouts"]}
    assert row["makers_paid_on_time"]["due"] == len(due)
    assert 0 <= row["makers_paid_on_time"]["paid"] <= len(due)
    assert row["llm_calls"] == 0 and row["tokens"] == {"in": 0, "out": 0}


# Scenarios the model-disabled Engine completes today; see the walkthrough for the rest.
COUNTERSIGN_SMOKE = [
    "clean_run_three_makers",
    "F_timeout_connection_reset_after_commit",
    "F_crash_second_line_503_after_commit",
    "F_partial_run_second_payout_500_recorded",
    "F_webhook_duplicate_item_succeeded",
]


@pytest.fixture(scope="module")
def library_database():
    base = os.environ.get("COUNTERSIGN_TEST_DATABASE_URL")
    if not base:
        pytest.skip("COUNTERSIGN_TEST_DATABASE_URL is unset")
    name = "cs_m14_" + uuid4().hex + "_eval"
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
        assert name.startswith("cs_m14_") and name.endswith("_eval")
        with psycopg.connect(base, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


@pytest.mark.integration
@pytest.mark.parametrize("name", COUNTERSIGN_SMOKE)
def test_countersign_engine_smoke_on_library_scenarios(name, library_database):
    from eval.systems.countersign import CountersignSystem

    async def run():
        system = CountersignSystem(database_url=library_database)
        row = await run_one(LIBRARY[name], system, run_id="m14-smoke", k_index=0, model="none")
        assert row["error"] is None
        assert not any(row["invariants"].values())
        assert row["passed"] and row["llm_calls"] == 0

    with asyncio.Runner(
        loop_factory=asyncio.SelectorEventLoop
        if sys.platform == "win32"
        else asyncio.new_event_loop
    ) as runner:
        runner.run(run())
