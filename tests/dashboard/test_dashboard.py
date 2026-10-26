import asyncio
import json
from copy import deepcopy
from pathlib import Path

import pytest
from schema_check import validate

SCHEMAS = Path(__file__).with_name("schemas")
RESOURCES = [
    "incidents",
    "proposals",
    "payout-runs",
    "makers",
    "disputes",
    "reservations",
    "audit-log",
]


@pytest.mark.parametrize("resource", RESOURCES)
@pytest.mark.parametrize("client", ["fixtures", "database"], indirect=True)
def test_list_contract_schemas(client, resource):
    response = client.get("/api/v1/" + resource)
    assert response.status_code == 200
    validate(response.json(), json.loads((SCHEMAS / (resource + ".json")).read_text()))


@pytest.mark.parametrize(
    "resource", ["incidents", "proposals", "payout-runs", "disputes"]
)
@pytest.mark.parametrize("client", ["fixtures", "database"], indirect=True)
def test_detail_contract_schemas(client, resource):
    id = client.get("/api/v1/" + resource).json()["items"][0]["id"]
    response = client.get("/api/v1/" + resource + "/" + id)
    assert response.status_code == 200
    validate(
        response.json(), json.loads((SCHEMAS / (resource + "-detail.json")).read_text())
    )


@pytest.mark.parametrize("resource", ["meta", "cash"])
@pytest.mark.parametrize("client", ["fixtures", "database"], indirect=True)
def test_singleton_contract_schemas(client, resource):
    response = client.get("/api/v1/" + resource)
    validate(response.json(), json.loads((SCHEMAS / (resource + ".json")).read_text()))


@pytest.mark.parametrize("client", ["fixtures", "database"], indirect=True)
def test_money_graph(client):
    id = client.get("/api/v1/disputes").json()["items"][0]["id"]
    response = client.get("/api/v1/money-graph", params={"dispute_id": id})
    validate(response.json(), json.loads((SCHEMAS / "money-graph.json").read_text()))
    assert client.get("/api/v1/money-graph").status_code == 400
    assert (
        client.get(
            "/api/v1/money-graph", params={"maker_id": "x", "run_id": "y"}
        ).status_code
        == 400
    )
    assert client.get("/api/v1/money-graph", params={"maker_id": "missing"}).json() == {
        "nodes": [],
        "edges": [],
    }


def test_pagination_filters_and_cursor_scope(client):
    url = "/api/v1/incidents"
    first = client.get(url, params={"limit": 1, "kind": "DISPUTE"}).json()
    assert len(first["items"]) == 1
    second = client.get(
        url, params={"limit": 1, "kind": "DISPUTE", "cursor": first["next_cursor"]}
    ).json()
    assert first["items"][0]["id"] != second["items"][0]["id"]
    assert second["next_cursor"] is None
    assert client.get(url, params={"cursor": first["next_cursor"]}).status_code == 400
    assert (
        client.get(
            "/api/v1/makers", params={"cursor": first["next_cursor"]}
        ).status_code
        == 400
    )
    assert client.get(url, params={"cursor": "bad"}).status_code == 400
    assert (
        len(
            client.get(
                url, params={"kind": "DISPUTE", "state": "AWAITING_OWNER"}
            ).json()["items"]
        )
        == 2
    )
    for limit in [0, 201, -1]:
        assert client.get(url, params={"limit": limit}).status_code == 422


@pytest.mark.parametrize(
    "resource,field,value",
    [
        ("proposals", "verdict", "BLOCK"),
        ("proposals", "action", "CANCEL_UNCLAIMED_PAYOUT"),
        ("proposals", "state", "NEEDS_OWNER"),
        ("payout-runs", "state", "AWAITING_OWNER"),
        ("disputes", "phase", "ACTIVE"),
        ("disputes", "status", "WAITING_FOR_SELLER_RESPONSE"),
        ("reservations", "kind", "DISPUTE_EARMARK"),
        ("reservations", "state", "ACTIVE"),
        ("audit-log", "actor", "shield"),
        ("audit-log", "kind", "SHIELD_VERDICT"),
    ],
)
def test_filters(client, resource, field, value):
    response = client.get("/api/v1/" + resource, params={field: value})
    assert response.status_code == 200
    assert response.json()["items"]
    assert all(item[field] == value for item in response.json()["items"])


def test_audit_since_and_missing_errors(client):
    assert (
        client.get("/api/v1/audit-log?since=2026-12-01T00:00:00Z").json()["items"] == []
    )
    assert client.get("/api/v1/audit-log?since=not-a-date").status_code == 400
    assert client.get("/api/v1/makers?unknown=x").status_code == 400
    for resource in ["incidents", "proposals", "payout-runs", "disputes"]:
        response = client.get("/api/v1/" + resource + "/missing")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"


def test_fixture_source_returns_copies_and_masks_emails(source, client):
    async def run():
        rows, _ = await source.list("makers", {}, 50, None)
        rows[0]["display_name"] = "changed"
        again, _ = await source.list("makers", {}, 50, None)
        assert again[0]["display_name"] != "changed"

    asyncio.run(run())
    data = json.dumps(client.get("/api/v1/proposals").json())
    assert "attacker@example.com" not in data
    assert "a***@example.com" in data


def test_schema_rejects_wrong_money_and_missing_fields(client):
    data = client.get("/api/v1/incidents").json()
    schema = json.loads((SCHEMAS / "incidents.json").read_text())
    wrong = deepcopy(data)
    wrong["items"][0]["amount_at_risk"]["minor"] = 1.2
    with pytest.raises(AssertionError):
        validate(wrong, schema)
    wrong = deepcopy(data)
    del wrong["items"][0]["state"]
    with pytest.raises(AssertionError):
        validate(wrong, schema)


def test_meta_and_run_consistency(client):
    meta = client.get("/api/v1/meta").json()
    assert (
        meta["simulated"] is True
        and meta["simulation_label"] == "Simulated: mock PayPal"
    )
    assert meta["contract_version"] == "1.0.3"
    run = client.get("/api/v1/payout-runs").json()["items"][0]
    detail = client.get("/api/v1/payout-runs/" + run["id"]).json()
    assert len(detail["lines"]) == run["line_count"]
    total = sum(
        line["amount"]["minor"] for line in detail["lines"] + detail["replacements"]
    )
    assert total == run["total"]["minor"]
    assert client.post("/api/v1/proposals/anything/approve", json={}).status_code in {
        404,
        405,
    }


def test_run_graph_and_reserve_totals(client):
    run = client.get("/api/v1/payout-runs").json()["items"][0]
    graph = client.get("/api/v1/money-graph", params={"run_id": run["id"]}).json()
    assert any(node["type"] == "line" for node in graph["nodes"])
    reservations = client.get("/api/v1/reservations").json()["items"]
    assert (
        sum(r["amount"]["minor"] for r in reservations if not r["affects_cash"])
        == 41400
    )
    proposals = client.get("/api/v1/proposals?action=SCHEDULE_PAYOUT_RUN").json()[
        "items"
    ]
    assert len(proposals[0]["params"]["lines"]) == 2
