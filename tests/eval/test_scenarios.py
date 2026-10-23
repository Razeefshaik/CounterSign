import copy
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from eval.validate import Scenario, main, seconds, validate
from mock_paypal.app import create_app

FOLDER = Path(__file__).resolve().parents[2] / "eval/scenarios"
FILES = sorted(FOLDER.glob("*.json"))


def scenario():
    return json.loads((FOLDER / "F01_timeout_after_payout_created.json").read_text())


@pytest.mark.parametrize("path", FILES, ids=lambda path: path.stem)
def test_six_scenarios_validate_and_seed_mock(path):
    raw = json.loads(path.read_text())
    doc = validate(raw)
    assert doc.id == path.stem
    assert doc.provenance == "synthesised"
    with TestClient(create_app(control_enabled=True)) as client:
        response = client.post("/_mock/seed", json=raw["initial_state"]["paypal"])
        assert response.status_code == 200
        truth = client.get("/_mock/ground-truth").json()
        assert (
            truth["balances"]["available"]["value"]
            == raw["initial_state"]["paypal"]["balances"]["available"]
        )


def test_schema_is_generated_from_validator():
    path = FOLDER.parent / "scenario.schema.json"
    schema = json.loads(path.read_text())
    expected = Scenario.model_json_schema()
    expected["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    expected["title"] = "CounterSign scenario · CONTRACTS v1.0.1"
    assert schema == expected
    assert len(schema["$defs"]["FaultStep"]["properties"]) == 3
    assert len(schema["properties"]["fault_class"]["enum"]) == 21
    assert len(schema["properties"]["timeline"]["items"]["oneOf"]) == 8
    assert schema["additionalProperties"] is False


def test_reference_checks():
    for change in [
        lambda data: data["initial_state"]["orders"][0].update(maker="missing"),
        lambda data: data["expected"]["payouts"][0].update(maker="missing"),
        lambda data: data["expected"].update(no_payout_to=["missing"]),
        lambda data: data["initial_state"]["orders"][0].update(capture_id="missing"),
        lambda data: data["initial_state"]["makers"].append(
            copy.deepcopy(data["initial_state"]["makers"][0])
        ),
        lambda data: data["initial_state"]["orders"].append(
            copy.deepcopy(data["initial_state"]["orders"][0])
        ),
        lambda data: data["initial_state"]["disputes"].append({"order": "missing"}),
        lambda data: data["initial_state"]["prior_payouts"].append(
            {"maker": "missing"}
        ),
    ]:
        data = scenario()
        change(data)
        with pytest.raises(ValidationError):
            validate(data)


@pytest.mark.parametrize("offset", ["-1h", "+1.5h", "+1s", "tomorrow", "+h"])
def test_invalid_offsets(offset):
    data = scenario()
    data["timeline"][0]["at"] = offset
    with pytest.raises(ValidationError):
        validate(data)


def test_timeline_order_and_duplicate_offsets():
    data = scenario()
    data["timeline"][0]["at"] = "+1h"
    with pytest.raises(ValidationError):
        validate(data)
    assert seconds("+90m") == 5400
    assert seconds("+3d") == 259200
    assert validate(scenario()).timeline[0].at == "+0h"


@pytest.mark.parametrize("value", [1.25, True, "48600"])
def test_internal_money_is_strict_integer(value):
    data = scenario()
    data["expected"]["payouts"][0]["amount"]["minor"] = value
    with pytest.raises(ValidationError):
        validate(data)


@pytest.mark.parametrize("value", [1.25, True, "1.2", "NaN", "1e3"])
def test_paypal_amounts_are_decimal_strings(value):
    data = scenario()
    data["initial_state"]["paypal"]["captures"][0]["amount"] = value
    with pytest.raises(ValidationError):
        validate(data)


def test_currency_and_counts_and_unknown_fields():
    for mutate in [
        lambda data: data["expected"]["payouts"][0]["amount"].update(currency="EUR"),
        lambda data: data["expected"]["payouts"][0].update(count=0),
        lambda data: data.update(unknown="not in contract"),
        lambda data: data.update(start="2026-11-02T09:00:00+01:00"),
        lambda data: data["timeline"][-1].update(repeat=0),
    ]:
        data = scenario()
        mutate(data)
        with pytest.raises(ValidationError):
            validate(data)


def test_mock_urls_and_sandbox_safety():
    data = scenario()
    data["timeline"] = [
        {
            "at": "+0h",
            "do": "mock",
            "method": "POST",
            "path": "https://api-m.paypal.com/v1/payments/payouts",
            "body": {},
        }
    ]
    with pytest.raises(ValidationError):
        validate(data)
    data = scenario()
    data["ground"] = "sandbox"
    with pytest.raises(ValidationError):
        validate(data)
    data["timeline"] = [{"at": "+0h", "do": "tick"}]
    assert validate(data).ground == "sandbox"


def test_owner_policy_and_buyer_references():
    data = scenario()
    data["timeline"] = [{"at": "+0h", "do": "owner", "policy": "approve_after"}]
    with pytest.raises(ValidationError):
        validate(data)
    data["timeline"][0]["seconds"] = 60
    assert validate(data).timeline[0].seconds == 60
    data["timeline"] = [
        {
            "at": "+0h",
            "do": "buyer_message",
            "dispute": "missing",
            "content": "Not permission",
        }
    ]
    with pytest.raises(ValidationError):
        validate(data)


def test_fault_parameters_and_regex():
    for action in [
        {"type": "drop_response"},
        {"type": "timeout", "after_commit": True},
        {"type": "http_error", "after_commit": False},
        {"type": "delay", "seconds": -1},
    ]:
        data = scenario()
        data["timeline"][0]["fault"]["action"] = action
        with pytest.raises(ValidationError):
            validate(data)
    data = scenario()
    data["timeline"][0]["fault"]["match"]["path"] = "["
    with pytest.raises(ValidationError):
        validate(data)


def test_cli_checks_all_scenarios_and_redacts_bad_input(tmp_path, capsys):
    assert len(FILES) == 6
    assert main([str(path) for path in FILES]) == 0
    bad = scenario()
    bad["start"] = "private-invalid-input"
    path = tmp_path / (bad["id"] + ".json")
    path.write_text(json.dumps(bad))
    assert main([str(path)]) == 1
    assert "private-invalid-input" not in capsys.readouterr().out


def test_demo_week_matches_handoff_amounts():
    raw = json.loads((FOLDER / "demo_week.json").read_text())
    doc = validate(raw)
    assert sum(p.amount.minor * p.count for p in doc.expected.payouts) == 108600
    assert doc.expected.owner_should_approve[0].total.minor == 108600
    assert doc.expected.no_payout_to == ["A"]
    assert doc.expected.refunds == []
    assert doc.initial_state.paypal.trackers[0]["shipment_uploader"] == "MERCHANT"
