"""Offline audit of genuine read-tool metadata; never calls a read or model."""

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError, validate

from countersign.agent.codex_cli import schema_for
from countersign.agent.codex_cli_schema import decode_calls, strict_parameters
from countersign.agent.models import ModelError
from countersign.agent.reads import custom_tools
from countersign.agent.tools import ALLOWED_TOOLS

FIXTURE = Path(__file__).resolve().parents[3] / "design-demos/reference/c67/stage1-repeat/read-tool-schemas.json"


def tools():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def assert_strict(node):
    if isinstance(node, dict):
        if node.get("type") == "object":
            assert node["additionalProperties"] is False
            assert set(node["required"]) == set(node["properties"])
        assert "default" not in node
        for child in node.values():
            assert_strict(child)
    elif isinstance(node, list):
        for child in node:
            assert_strict(child)


def test_real_eight_schemas_are_strict_on_output_only():
    original = tools()
    before = copy.deepcopy(original)
    assert {t["function"]["name"] for t in original} == ALLOWED_TOOLS
    # Snapshot's three application schemas still match the live implementation.
    for tool in custom_tools(None, None):
        captured = next(t for t in original if t["function"]["name"] == tool.name)
        assert captured["function"]["parameters"] == tool.schema
    output = schema_for(original)
    Draft202012Validator.check_schema(output)
    assert_strict(output)
    assert original == before
    assert_strict(schema_for([]))
    with pytest.raises(ValidationError):
        validate({"tool_calls": [{}], "proposal_json": ""}, schema_for([]))


@pytest.mark.parametrize("name", ["get_shipment_tracking", "list_disputes", "list_transactions",
                                  "query_money_graph", "simulate"])
def test_omitted_optional_arguments_stay_omitted(name):
    definitions = tools()
    original = next(t["function"]["parameters"] for t in definitions if t["function"]["name"] == name)
    required = {"query": "cash", "run_id": "RUN-C67"}
    wire = {key: required[key] if key in original.get("required", []) else None
            for key in original["properties"]}
    calls = decode_calls([{"id": "read1", "name": name, "args": wire}], definitions)
    assert calls[0]["args"] == {key: required[key] for key in original.get("required", [])}
    validate(calls[0]["args"], original)


def test_optional_defaults_explicit_null_and_explicit_value_remain_distinct():
    original = next(t["function"]["parameters"] for t in tools() if t["function"]["name"] == "list_disputes")
    def decode(page_size):
        return decode_calls([{"id": "read1", "name": "list_disputes", "args": {
            "disputed_transaction_id": None, "dispute_state": None, "page_size": page_size}}], tools())[0]["args"]
    assert original["properties"]["page_size"]["default"] == 10
    assert decode(None) == {}
    assert decode({"value": None}) == {"page_size": None}
    assert decode({"value": 20}) == {"page_size": 20}


@pytest.mark.parametrize("args", [
    {"query": "cash", "id": None, "limit": {"value": 101}},
    {"query": "money_write", "id": None, "limit": None},
    {"query": "cash", "id": {"value": None}, "limit": None},
    {"query": "cash", "id": None, "limit": None, "write": True},
    {"query": "cash", "id": None},
])
def test_wire_encoding_cannot_widen_original_tools(args):
    with pytest.raises(ModelError, match="^INVALID_MODEL_RESPONSE$"):
        decode_calls([{"id": "read1", "name": "query_money_graph", "args": args}], tools())


def test_nested_optional_encoding_round_trips_without_losing_presence():
    original = {"type": "object", "additionalProperties": False, "properties": {
        "rows": {"type": "array", "items": {"type": "object", "additionalProperties": False,
            "properties": {"note": {"anyOf": [{"type": "string"}, {"type": "null"}]}},
            "required": []}}}, "required": ["rows"]}
    strict = strict_parameters(original)
    assert_strict(strict)
    definitions = [{"function": {"name": "simulate", "parameters": original}}]
    result = decode_calls([{"id": "r", "name": "simulate", "args": {"rows": [
        {"note": None}, {"note": {"value": None}}, {"note": {"value": "hello"}}]}}], definitions)
    assert result[0]["args"] == {"rows": [{}, {"note": None}, {"note": "hello"}]}


def test_arbitrary_objects_are_refused_instead_of_widened():
    with pytest.raises(ModelError, match="MODEL_TOOL_SCHEMA_UNSUPPORTED"):
        strict_parameters({"type": "object", "additionalProperties": True})
