"""Recorded provenance and metrics, with planted bad data; no provider access."""

import json
from copy import deepcopy
from pathlib import Path

import pytest

from scripts.bundle_real_model import bundle, validated_evaluation
from scripts.c67_recording import project_decision

ROOT = Path(__file__).resolve().parents[2]


def rows():
    result = []
    for system in ("countersign", "baseline_llm", "rules_only"):
        result.append({"system": system, "scenario_id": "case", "k": 0,
            "fault_class": "TIMEOUT", "provenance": "synthesised", "passed": False,
            "correct_final_action": False, "task_completed": False,
            "invariants": {"duplicate_settlement": 1, "payment_to_unverified_payee": 0,
                "payment_on_disputed_order_without_approval": 0, "refund_without_owner_approval": 0},
            "makers_paid_on_time": {"paid": 0, "due": 1}, "owner_escalations": 0,
            "unnecessary_escalations": 0, "duration_ms": 10,
            "model": "none" if system == "rules_only" else "google/gemma-4-31b-it:free",
            "ground": "mock", "llm_calls": int(system != "rules_only"),
            "tokens": {"in": 12, "out": 3}, "error": None})
    return result


def test_invariant_totals_are_recomputed_and_bad_results_remain_failed():
    value = validated_evaluation(rows(), k=1, scenario_ids={"case"},
                                model="google/gemma-4-31b-it:free")
    assert len(value["summary"]) == 3
    for system in value["summary"]:
        assert system["invariant_violations"]["duplicate_settlement"] == 1
        assert system["pass_rate"] == 0 and system["task_completion_rate"] == 0


@pytest.mark.parametrize("change", ["partial", "duplicate", "wrong_model", "inconsistent_pass"])
def test_invalid_or_incomplete_experiment_cannot_be_published(change):
    records = rows()
    if change == "partial":
        records.pop()
    elif change == "duplicate":
        records.append(deepcopy(records[0]))
    elif change == "wrong_model":
        records[0]["model"] = "google/gemini-3.5-flash"
    else:
        records[0]["passed"] = True
    with pytest.raises(ValueError):
        validated_evaluation(records, k=1, scenario_ids={"case"},
                             model="google/gemma-4-31b-it:free")


def test_recording_contains_observed_fields_only_and_no_private_assistant_history():
    doc = {"id": "proposal", "action": "WAIT", "rationale": "The state is unknown.",
           "flags": [], "verdict": "ALLOW", "raw_output": {"private_signature": "opaque"}}
    trace = {"calls": [{"tool": "get_dispute", "arguments": {"dispute_id": "case"},
                       "ok": True, "error_code": None}], "rounds": 2}
    value = project_decision(doc, trace)
    assert value["reads"] == trace["calls"] and value["rationale"] == doc["rationale"]
    assert value["flags"] == [] and "raw_output" not in value and "opaque" not in json.dumps(value)


def test_committed_bundle_matches_sources_and_does_not_invent_new_scores():
    expected = bundle(ROOT)
    assert json.loads((ROOT / "web/results/real-model-evidence.json").read_text()) == expected
    assert len(expected["weeks"]) == 1 and not expected["smokes"]
    earlier = expected["weeks"][0]
    assert earlier["model"] == "gemini-3.5-flash-lite"
    assert all(row["rationale"] is None and row["reads"] is None for row in earlier["decisions"])
    assert sum(row["action"] == "WAIT" for row in earlier["decisions"]) == 2
    assert not expected["datasets"][0]["summary"] and not expected["datasets"][0]["runs"]
    assert expected["datasets"][0]["label"] == "Real AI agent, mock PayPal"


@pytest.mark.parametrize("private", ["sk-or-v1-" + "q" * 64,
    "Bearer " + "s" * 30, "real.person@private-company.invalid", "OPEN_ROUTER_API_KEY=private"])
def test_publication_rejects_planted_credentials_and_real_emails(private):
    records = rows()
    records[0]["error"] = private
    with pytest.raises(ValueError):
        validated_evaluation(records, k=1, scenario_ids={"case"},
                             model="google/gemma-4-31b-it:free")
