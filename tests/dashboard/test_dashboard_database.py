"""M15: real PostgreSQL boundaries, pagination, and application integration."""

import asyncio
import base64
import json
import os
import sys
from types import SimpleNamespace
from urllib.parse import urlencode

import httpx
import psycopg
import pytest
from conftest import dashboard


def run(coro):
    with asyncio.Runner(
        loop_factory=asyncio.SelectorEventLoop
        if sys.platform == "win32"
        else asyncio.new_event_loop
    ) as runner:
        return runner.run(coro)


@pytest.mark.parametrize("resource", list(dashboard.DbSource.views))
def test_database_keyset_pages(database_client, resource):
    whole = database_client.get(f"/api/v1/{resource}").json()["items"]
    collected, cursor = [], None
    for _ in range(len(whole) + 1):
        params = {"limit": 1}
        if cursor:
            params["cursor"] = cursor
        response = database_client.get(f"/api/v1/{resource}", params=params)
        assert response.status_code == 200
        page = response.json()
        collected.extend(page["items"])
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert collected == whole


def test_cursor_binding_and_parameterized_filters(database_client):
    first = database_client.get("/api/v1/incidents", params={"limit": 1}).json()
    cursor = first["next_cursor"]
    assert cursor
    for path, params in [
        ("incidents", {"cursor": "not-base64"}),
        ("makers", {"cursor": cursor}),
        ("incidents", {"cursor": cursor, "state": "OPEN"}),
        ("incidents", {"unknown_column": "x"}),
        ("audit-log", {"since": "2026-10-27"}),
    ]:
        assert database_client.get(f"/api/v1/{path}", params=params).status_code == 400
    response = database_client.get(
        "/api/v1/incidents", params={"state": "'; DROP TABLE incidents; --"}
    )
    assert response.status_code == 200
    assert response.json()["items"] == []
    assert database_client.get("/api/v1/incidents").json()["items"]
    malformed = json.loads(base64.urlsafe_b64decode(cursor))
    malformed["id"] = 123
    invalid_id = base64.urlsafe_b64encode(json.dumps(malformed).encode()).decode()
    assert (
        database_client.get("/api/v1/incidents", params={"cursor": invalid_id}).status_code == 400
    )


def test_maker_cursor_does_not_encode_private_display_name(database_client):
    page = database_client.get("/api/v1/makers", params={"limit": 2}).json()
    cursor = json.loads(base64.urlsafe_b64decode(page["next_cursor"]))
    assert cursor["value"] is None
    response = database_client.get("/api/v1/makers", params={"cursor": page["next_cursor"]})
    assert response.status_code == 200
    assert "private@example.test" not in response.text
    assert len(response.json()["items"]) == 1


def test_missing_cash_never_falls_back_to_fixture(database_client, dashboard_database):
    with psycopg.connect(dashboard_database.conninfo, autocommit=True) as conn:
        conn.execute("DELETE FROM balance_snapshots")
    response = database_client.get("/api/v1/cash")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DATA_UNAVAILABLE"
    assert database_client.get("/api/v1/incidents/not-a-uuid").status_code == 404


def test_newer_insert_does_not_shift_existing_page(database_client, dashboard_database):
    first = database_client.get("/api/v1/incidents", params={"limit": 1}).json()
    with psycopg.connect(dashboard_database.conninfo, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO incidents (id,kind,state,opened_at,subject) "
            "VALUES ('019a12de-7598-7000-8000-00000000fffe',"
            "'DISPUTE','AWAITING_OWNER',now(),'{}')"
        )
    second = database_client.get(
        "/api/v1/incidents", params={"limit": 1, "cursor": first["next_cursor"]}
    ).json()
    assert len(second["items"]) == 1
    assert second["items"][0]["id"] != first["items"][0]["id"]
    assert second["next_cursor"] is None


def test_query_failure_uses_value_free_envelope(database_client, dashboard_database):
    with psycopg.connect(dashboard_database.conninfo, autocommit=True) as conn:
        conn.execute("DROP VIEW v_dash_incident_detail")
    response = database_client.get(f"/api/v1/incidents/{dashboard_database.ids['incident']}")
    assert response.status_code == 503
    assert response.json() == {
        "error": {"code": "DATA_UNAVAILABLE", "message": "Dashboard database unavailable"}
    }
    assert database_client.get("/api/v1/makers").status_code == 200


def test_connection_is_read_only_and_cannot_read_private_tables(dashboard_database):
    async def check():
        runtime = SimpleNamespace(mode="demo", paypal=SimpleNamespace(is_mock=True))
        source = dashboard.DbSource(dashboard_database.conninfo, runtime=runtime)
        async with source:
            async with source._read() as conn:
                row = await (await conn.execute("SHOW transaction_read_only")).fetchone()
                assert row["transaction_read_only"] == "on"
                role = await (await conn.execute("SELECT current_role AS role")).fetchone()
                assert role["role"] == "countersign_agent_ro"
            for statement in ["SELECT * FROM makers", "DELETE FROM makers"]:
                with pytest.raises(dashboard.DashboardUnavailable, match="database unavailable"):
                    async with source._read() as conn:
                        await conn.execute(statement)
            assert (await source.list("makers", {}, 50, None))[0]
        assert source.pool.closed

    run(check())


def test_fixture_default_and_invalid_configuration(monkeypatch):
    async def check():
        runtime = SimpleNamespace(mode="live", paypal=SimpleNamespace(is_mock=False))
        monkeypatch.delenv("COUNTERSIGN_DASHBOARD_SOURCE", raising=False)
        async with dashboard.configured_source(runtime) as source:
            assert source.source_name == "fixtures"
            meta = await source.singleton("meta", {})
            assert meta["mode"] == "live"
            assert meta["simulated"] is False
        monkeypatch.setenv("COUNTERSIGN_DASHBOARD_SOURCE", "typo")
        with pytest.raises(ValueError, match="Dashboard source"):
            async with dashboard.configured_source(runtime):
                pytest.fail("Invalid source must not fall back to fixtures")

    run(check())


def test_application_uses_database_and_truthful_header(dashboard_database, monkeypatch):
    from countersign.config import Settings
    from countersign.main import create_app

    monkeypatch.setenv("COUNTERSIGN_DASHBOARD_SOURCE", "database")
    base = os.environ["COUNTERSIGN_TEST_DATABASE_URL"]
    database_url = (
        base
        + ("&" if "?" in base else "?")
        + urlencode({"options": f"-csearch_path={dashboard_database.schema},public"})
    )

    async def check():
        app = create_app(
            settings=Settings(database_url=database_url),
            database=object(),
            reader=object(),
            verifier=object(),
            start_worker=False,
        )
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://testserver"
            ) as client,
        ):
            response = await client.get("/api/v1/incidents")
            meta = (await client.get("/api/v1/meta")).json()
        assert response.status_code == 200
        assert response.headers["X-CounterSign-Dashboard-Source"] == "database"
        assert {item["id"] for item in response.json()["items"]} == {
            str(dashboard_database.ids[key]) for key in ["incident", "incident2"]
        }
        assert meta["simulation_label"] == "Simulated: mock PayPal"

    run(check())
