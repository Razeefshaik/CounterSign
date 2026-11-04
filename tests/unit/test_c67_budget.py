import json
from decimal import Decimal

import httpx
import pytest

from countersign.agent.models import ModelError
from scripts.c67_budget import OUTPUT_LIMIT, Budget


def test_budget_reserves_before_send_and_keeps_unknown_failed_cost(tmp_path):
    budget = Budget(tmp_path / "budget.json", dollars="0.05", requests=6)
    payload = {"model": "google/gemini-3.5-flash", "messages": []}
    budget.before_request(payload)
    assert payload["max_tokens"] == OUTPUT_LIMIT
    budget.after_response(httpx.Response(503))
    with pytest.raises(ModelError, match="C67_BUDGET_EXHAUSTED"):
        budget.before_request(payload)
    assert len(budget.state["requests"]) == 1
    assert budget.state["requests"][0]["ceiling_charge_usd"] != "0"


def test_usage_and_request_limit_survive_resume(tmp_path):
    path = tmp_path / "budget.json"
    budget = Budget(path, dollars="0.25", requests=1)
    budget.before_request({"model": "google/gemini-3.5-flash", "messages": []})
    budget.after_response(httpx.Response(200, json={"model": "google/gemini-3.5-flash",
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "cost": 0.00006}}))
    resumed = Budget(path, dollars="0.25", requests=1)
    assert resumed.charged == Decimal("0.00006")
    with pytest.raises(ModelError, match="C67_BUDGET_EXHAUSTED"):
        resumed.before_request({"model": "google/gemini-3.5-flash", "messages": []})
    assert len(json.loads(path.read_text())["requests"]) == 1


def test_budget_refuses_changed_limits_or_wrong_model(tmp_path):
    path = tmp_path / "budget.json"
    budget = Budget(path, dollars="0.25", requests=6)
    with pytest.raises(ModelError, match="MODEL_ID_MISMATCH"):
        budget.before_request({"model": "different"})
    with pytest.raises(ValueError, match="C67_BUDGET_RESUME_MISMATCH"):
        Budget(path, dollars="30", requests=2500)


def test_free_model_enforces_zero_spend_and_shared_request_cap(tmp_path):
    budget = Budget(tmp_path / "free.json", dollars="0", requests=2,
                    model="google/gemma-4-31b-it:free")
    payload = {"model": "google/gemma-4-31b-it:free", "messages": []}
    budget.before_request(payload)
    budget.after_response(httpx.Response(200, json={"model": "google/gemma-4-31b-it",
        "usage": {"prompt_tokens": 100, "completion_tokens": 500, "cost": 0}}))
    assert budget.charged == 0
    budget.before_request(payload)
    budget.after_response(httpx.Response(429))
    with pytest.raises(ModelError, match="C67_BUDGET_EXHAUSTED"):
        budget.before_request(payload)
