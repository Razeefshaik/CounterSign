"""C20's legitimate records must fit the public schemas without local patching."""

import json
from copy import deepcopy
from pathlib import Path

import pytest
from schema_check import validate

SCHEMAS = Path(__file__).with_name("schemas")
LOCATIONS = ["proposals", "proposals-detail", "incidents-detail"]


def proposal_document(client, location):
    proposals = client.get("/api/v1/proposals").json()
    owner = next(p for p in proposals["items"] if p["state"] == "NEEDS_OWNER")
    if location == "proposals":
        document, target = proposals, owner
    elif location == "proposals-detail":
        document = client.get("/api/v1/proposals/" + owner["id"]).json()
        target = document
    else:
        document = client.get("/api/v1/incidents/" + owner["incident_id"]).json()
        target = next(p for p in document["proposals"] if p["id"] == owner["id"])
    schema = json.loads((SCHEMAS / (location + ".json")).read_text())
    return document, target, schema


@pytest.mark.parametrize("location", LOCATIONS)
def test_contract_102_accepts_injection_guard_rule_results(client, location):
    document, target, schema = proposal_document(client, location)
    target["rule_results"].append(
        {"rule": "INJECTION_GUARD", "outcome": "BLOCK", "detail": "Untrusted financial instruction"}
    )
    validate(document, schema)


@pytest.mark.parametrize("location", LOCATIONS)
def test_proposal_incident_link_can_be_null_but_not_wrong_type(client, location):
    document, target, schema = proposal_document(client, location)
    target["incident_id"] = None
    validate(document, schema)
    target["incident_id"] = 7
    with pytest.raises(AssertionError):
        validate(document, schema)


@pytest.mark.parametrize("field", ["dispute_id", "proposal_id", "run_id", "obligation_id"])
def test_reservation_links_accept_null_and_ids_only(client, field):
    document = client.get("/api/v1/reservations").json()
    schema = json.loads((SCHEMAS / "reservations.json").read_text())
    for value in [None, "0192f1c4-7a10-7c3e-9b1a-5d2f00000001"]:
        document["items"][0][field] = value
        validate(document, schema)
    for value in [False, 3, {}, []]:
        document["items"][0][field] = value
        with pytest.raises(AssertionError):
            validate(document, schema)


@pytest.mark.parametrize("location", LOCATIONS)
def test_version_map_accepts_arbitrary_record_ids_with_positive_integer_versions(client, location):
    document, target, schema = proposal_document(client, location)
    versions = {"obligation:0192f1c4-7a10-7c3e-9b1a-5d2f00000801": 3, "maker:another-id": 2}
    target["approval_request"]["record_versions"] = versions
    validate(document, schema)
    for value in ["3", False, 0, -1, 1.5, None]:
        target["approval_request"]["record_versions"]["line:new-id"] = value
        with pytest.raises(AssertionError):
            validate(document, schema)


@pytest.mark.parametrize("location", LOCATIONS)
@pytest.mark.parametrize(
    "state",
    [
        "CREATED",
        "ALLOWED",
        "BLOCKED",
        "NEEDS_OWNER",
        "APPROVED",
        "REJECTED",
        "EXPIRED",
        "EXECUTING",
        "EXECUTED",
        "BLOCKED_AT_EXECUTION",
        "FAILED",
    ],
)
def test_approval_request_exists_exactly_while_needing_owner(client, location, state):
    document, target, schema = proposal_document(client, location)
    request = deepcopy(target.pop("approval_request"))
    target["state"] = state
    if state == "NEEDS_OWNER":
        target["approval_request"] = request
    validate(document, schema)
    if state == "NEEDS_OWNER":
        del target["approval_request"]
    else:
        target["approval_request"] = request
    with pytest.raises(AssertionError):
        validate(document, schema)


def test_schema_checker_keeps_branch_and_additional_property_constraints():
    schema = {
        "anyOf": [{"type": "object"}],
        "properties": {"state": {"type": "string"}},
        "additionalProperties": {"type": "integer", "minimum": 1},
        "if": {"properties": {"state": {"enum": ["pending"]}}},
        "then": {"required": ["version"]},
        "else": {"not": {"required": ["version"]}},
    }
    validate({"state": "pending", "version": 2}, schema)
    validate({"state": "executed"}, schema)
    for document in [
        {"state": "pending"},
        {"state": "pending", "version": "2"},
        {"state": "executed", "version": 1},
    ]:
        with pytest.raises(AssertionError):
            validate(document, schema)
