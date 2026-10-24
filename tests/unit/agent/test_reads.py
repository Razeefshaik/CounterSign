"""Offline proofs of custom-tool boundaries, role checks and fixed queries."""

import asyncio
import importlib.util
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import httpx
import psycopg
import pytest
from psycopg import sql

from countersign.agent.reads import (
    CustomReads,
    MoneyGraphQueries,
    ReadToolError,
    custom_tools,
)
from countersign.config import PayPalSettings
from countersign.paypal.http import PayPalHTTP
from countersign.paypal.read import PayPalRead

ROW_ID = "0192f1c4-7a10-7c3e-9b1a-5d2f00000042"
PRIVATE_DSN = "postgresql://fake:FAKE-DATABASE-PASSWORD@localhost/example_test"
GOOD_ROLE = {
    "role": "countersign_agent_ro",
    "limited_role": True,
    "read_only": True,
    "postgres_16": True,
    "no_schema_create": True,
    "no_memberships": True,
    "view_select_only": True,
}


class Cursor:
    def __init__(self, *, row=None, rows=None):
        self.row, self.rows = row, rows

    async def fetchone(self):
        return self.row

    async def fetchall(self):
        return self.rows


class Connection:
    def __init__(self, *, role=None, rows=None, failure=None):
        self.role = dict(GOOD_ROLE) if role is None else role
        self.rows = [{"doc": {"id": ROW_ID}}] if rows is None else rows
        self.failure = failure
        self.calls = []
        self.closed = False
        self.transaction_done = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        self.closed = True

    @asynccontextmanager
    async def transaction(self):
        try:
            yield self
        finally:
            self.transaction_done = True

    async def execute(self, statement, params=None):
        rendered = statement if isinstance(statement, str) else statement.as_string()
        self.calls.append((rendered, params))
        if self.failure:
            raise self.failure
        if "FROM pg_roles" in rendered:
            return Cursor(row=self.role)
        return Cursor(rows=self.rows)


class Connector:
    def __init__(self, connection=None):
        self.connection = connection if connection is not None else Connection()
        self.calls = []

    async def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.connection


def graph(connection=None):
    connect = Connector(connection)
    return MoneyGraphQueries(PRIVATE_DSN, "cs_test_example", connect=connect), connect


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("query", "view", "filter_column"),
    [
        ("incident", "v_dash_incident_detail", "id"),
        ("dispute", "v_dash_disputes", "id"),
        ("maker", "v_dash_makers", "id"),
        ("run", "v_dash_run_detail", "id"),
        ("reservations_by_dispute", "v_dash_reservations", "dispute_id"),
        ("reservations_by_run", "v_dash_reservations", "run_id"),
        ("proposals_by_incident", "v_dash_proposals", "incident_id"),
    ],
)
async def test_fixed_graph_queries_bind_uuid_values_and_apply_restricted_role(
    query, view, filter_column
):
    queries, connect = graph()
    result = await queries.query({"query": query, "id": ROW_ID})
    conn = connect.connection
    commands = [call[0] for call in conn.calls]
    assert commands[:3] == [
        "SET TRANSACTION READ ONLY",
        "SET LOCAL search_path = pg_catalog",
        "SET LOCAL ROLE countersign_agent_ro",
    ]
    statement, params = conn.calls[-1]
    assert statement.startswith(f'SELECT doc FROM "cs_test_example"."{view}"')
    assert f'WHERE "{filter_column}" = %s' in statement
    assert ROW_ID not in statement
    assert params == (UUID(ROW_ID), 20 if query.startswith(("reservations_", "proposals_")) else 1)
    assert result == {"query": query, "items": [{"id": ROW_ID}]}
    assert connect.calls[0][1]["autocommit"] is True
    assert conn.closed and conn.transaction_done


@pytest.mark.asyncio
async def test_cash_query_binds_only_limit_and_does_not_need_an_identifier():
    queries, connect = graph()
    await queries.query({"query": "cash"})
    statement, params = connect.connection.calls[-1]
    assert statement == (
        'SELECT doc FROM "cs_test_example"."v_dash_cash" ORDER BY "currency" LIMIT %s'
    )
    assert params == (1,)


@pytest.mark.asyncio
async def test_graph_startup_checks_role_without_reading_model_data():
    queries, connect = graph()
    assert await queries.start() is queries
    assert len(connect.connection.calls) == 4
    assert connect.connection.closed
    assert PRIVATE_DSN not in repr(queries)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changed",
    [
        {"role": "postgres"},
        {"limited_role": False},
        {"read_only": False},
        {"postgres_16": False},
        {"no_schema_create": False},
        {"no_memberships": False},
        {"view_select_only": False},
        {"view_select_only": None},
    ],
)
async def test_role_or_privilege_drift_refuses_any_data_query(changed):
    connection = Connection(role=GOOD_ROLE | changed)
    queries, _ = graph(connection)
    with pytest.raises(ReadToolError, match="^MONEY_GRAPH_ROLE_REFUSED$"):
        await queries.query({"query": "cash"})
    assert len(connection.calls) == 4
    assert connection.closed and connection.transaction_done


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments",
    [
        {"query": "SELECT * FROM makers"},
        {"query": "maker; DROP TABLE makers", "id": ROW_ID},
        {"query": "maker", "id": "' OR 1=1 --"},
        {"query": "maker", "id": "https://example.com"},
        {"query": "maker"},
        {"query": "maker", "id": ROW_ID, "schema": "public"},
        {"query": "maker", "id": ROW_ID, "limit": 2},
        {"query": "cash", "id": ROW_ID},
        {"query": "cash", "limit": True},
        {"query": "reservations_by_run", "id": ROW_ID, "limit": 101},
        {"query": "reservations_by_run", "id": ROW_ID, "limit": 0},
        {"query": "reservations_by_run", "id": ROW_ID, "limit": 1.5},
        {},
        [],
    ],
)
async def test_invalid_graph_input_never_opens_a_connection(arguments):
    queries, connect = graph()
    with pytest.raises(ReadToolError):
        await queries.query(arguments)
    assert connect.calls == []


@pytest.mark.asyncio
async def test_graph_driver_errors_do_not_expose_private_connection_values():
    queries, _ = graph(Connection(failure=RuntimeError(PRIVATE_DSN)))
    with pytest.raises(ReadToolError, match="^MONEY_GRAPH_UNAVAILABLE$") as error:
        await queries.query({"query": "cash"})
    assert PRIVATE_DSN not in str(error.value)
    assert error.value.__suppress_context__ is True


@pytest.mark.asyncio
async def test_masked_view_results_are_sanitized_again_before_entering_model_context():
    connection = Connection(
        rows=[{"doc": {"id": ROW_ID, "note": "buyer@fake.example", "access_token": "FAKE"}}]
    )
    queries, _ = graph(connection)
    result = await queries.query({"query": "maker", "id": ROW_ID})
    assert "buyer@fake.example" not in str(result)
    assert "FAKE" not in str(result)
    assert result["items"][0]["id"] == ROW_ID


@pytest.mark.asyncio
@pytest.mark.parametrize("rows", [[{"doc": None}], [{"private": {}}], ["unexpected"]])
async def test_unexpected_view_shapes_fail_closed(rows):
    queries, _ = graph(Connection(rows=rows))
    with pytest.raises(ReadToolError, match="^INVALID_MONEY_GRAPH_RESPONSE$"):
        await queries.query({"query": "cash"})


def test_schema_configuration_does_not_allow_sql_or_system_catalogs():
    for schema in ("public; DROP TABLE makers", "pg_catalog", "information_schema", "", "x.y"):
        with pytest.raises(ReadToolError, match="^INVALID_GRAPH_SCHEMA$"):
            MoneyGraphQueries(PRIVATE_DSN, schema)


@pytest.mark.asyncio
async def test_payout_tool_uses_real_get_only_reader_and_redacted_body():
    requests = []
    secret = "FAKE-PAYPAL-SECRET"
    token = "FAKE-PAYPAL-TOKEN"

    def respond(request):
        requests.append((request.method, request.url.path))
        if request.url.path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": token, "expires_in": 3600})
        return httpx.Response(
            200,
            json={
                "payout_item_id": "MOCKITEM42",
                "transaction_status": "UNCLAIMED",
                "payout_item": {"receiver": "maker@fake.example"},
                "echo": f"{secret} {token}",
                "client_secret": secret,
            },
        )

    settings = PayPalSettings(client_id="FAKE-PAYPAL-CLIENT", client_secret=secret)
    async with PayPalHTTP(settings, transport=httpx.MockTransport(respond)) as http:
        queries, _ = graph()
        reads = CustomReads(PayPalRead(http), queries)
        result = await reads.get_payout_item({"payout_item_id": "MOCKITEM42"})
    assert requests == [
        ("POST", "/v1/oauth2/token"),
        ("GET", "/v1/payments/payouts-item/MOCKITEM42"),
    ]
    assert result["payout_item"]["payout_item_id"] == "MOCKITEM42"
    assert result["payout_item"]["transaction_status"] == "UNCLAIMED"
    for private in (secret, token, "maker@fake.example"):
        assert private not in str(result)


@pytest.mark.asyncio
async def test_payout_tool_never_falls_back_to_private_response_body():
    class Reader:
        async def get_payout_item(self, _):
            return SimpleNamespace(status_code=200, body={"private": "FAKE"}, safe_body=None)

    queries, _ = graph()
    reads = CustomReads(Reader(), queries)
    with pytest.raises(ReadToolError, match="^UNSAFE_PAYOUT_ITEM_RESPONSE$"):
        await reads.get_payout_item({"payout_item_id": "MOCKITEM42"})


@pytest.mark.asyncio
async def test_payout_tool_refuses_a_response_for_a_different_resource():
    class Reader:
        async def get_payout_item(self, _):
            return SimpleNamespace(status_code=200, safe_body={"payout_item_id": "OTHERITEM"})

    queries, _ = graph()
    with pytest.raises(ReadToolError, match="^INVALID_PAYOUT_ITEM_RESPONSE$"):
        await CustomReads(Reader(), queries).get_payout_item({"payout_item_id": "MOCKITEM42"})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "identifier", ["../MOCKITEM42", "https://example.com/item", "x?cancel=1", ""]
)
async def test_invalid_payout_ids_do_not_reach_reader(identifier):
    queries, _ = graph()
    reads = CustomReads(None, queries)
    with pytest.raises(ReadToolError, match="^INVALID_PAYOUT_ITEM_IDENTIFIER$"):
        await reads.get_payout_item({"payout_item_id": identifier})


@pytest.mark.asyncio
async def test_simulator_is_honestly_unavailable_by_default():
    queries, _ = graph()
    reads = CustomReads(None, queries)
    assert await reads.simulate({"run_id": ROW_ID}) == {
        "available": False,
        "reason": "SIMULATOR_UNAVAILABLE",
    }


@pytest.mark.asyncio
async def test_simulation_calls_only_explicit_read_only_dependency_with_validated_arguments():
    class ScriptedSimulator:
        def __init__(self):
            self.calls = []

        async def simulate(self, request):
            self.calls.append(request)
            return {"available_change": {"minor": -108600, "currency": "USD"}}

    simulator = ScriptedSimulator()
    queries, _ = graph()
    reads = CustomReads(None, queries, simulator)
    result = await reads.simulate({"run_id": ROW_ID, "respect_reserves": False})
    assert simulator.calls == [{"run_id": ROW_ID, "respect_reserves": False}]
    assert result == {
        "available": True,
        "simulation": {"available_change": {"minor": -108600, "currency": "USD"}},
    }


@pytest.mark.asyncio
async def test_simulator_cannot_receive_extra_parameters_or_non_boolean_reserve_choice():
    queries, _ = graph()
    reads = CustomReads(None, queries)
    for arguments in (
        {"run_id": ROW_ID, "respect_reserves": "false"},
        {"run_id": ROW_ID, "execute": True},
        {"run_id": "not-an-id"},
    ):
        with pytest.raises(ReadToolError):
            await reads.simulate(arguments)


@pytest.mark.asyncio
@pytest.mark.parametrize("minor", [1.5, True, "100"])
async def test_simulator_money_must_be_integer_minor_units(minor):
    class Simulator:
        async def simulate(self, request):
            return {"available_change": {"minor": minor, "currency": "USD"}}

    queries, _ = graph()
    with pytest.raises(ReadToolError, match="^INVALID_SIMULATION_RESPONSE$"):
        await CustomReads(None, queries, Simulator()).simulate({"run_id": ROW_ID})


def test_custom_tool_factory_exposes_only_the_three_documented_names():
    queries, _ = graph()
    tools = custom_tools(None, queries)
    assert [tool.name for tool in tools] == ["get_payout_item", "query_money_graph", "simulate"]
    assert all(tool.schema["additionalProperties"] is False for tool in tools)
    assert PRIVATE_DSN not in repr(tools)


@pytest.fixture
def graph_schema():
    """Reuse the existing isolated-schema migration/cleanup fixture unchanged."""
    if not os.environ.get("COUNTERSIGN_TEST_DATABASE_URL"):
        pytest.skip("COUNTERSIGN_TEST_DATABASE_URL is unset")
    path = Path(__file__).resolve().parents[2] / "integration" / "conftest.py"
    name = "_countersign_c23_schema_fixtures"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    schemas = module.schema_factory.__wrapped__()
    try:
        factory = next(schemas)
        yield factory()
    finally:
        schemas.close()
        sys.modules.pop(name, None)


@pytest.mark.integration
def test_live_postgres_role_reads_views_and_cannot_read_or_write_base_tables(graph_schema):
    async def probe():
        queries = MoneyGraphQueries(graph_schema.conninfo, graph_schema.schema)
        await queries.start()
        assert await queries.query({"query": "maker", "id": ROW_ID}) == {
            "query": "maker",
            "items": [],
        }
        async with queries._restricted() as conn:
            cursor = await conn.execute("SELECT current_user AS role")
            assert (await cursor.fetchone())["role"] == "countersign_agent_ro"
            for statement in (
                sql.SQL("SELECT * FROM {}.makers").format(sql.Identifier(graph_schema.schema)),
                sql.SQL("DELETE FROM {}.makers").format(sql.Identifier(graph_schema.schema)),
            ):
                # A savepoint keeps the read-only transaction usable after each refusal.
                with pytest.raises(
                    (psycopg.errors.InsufficientPrivilege, psycopg.errors.ReadOnlySqlTransaction)
                ):
                    async with conn.transaction():
                        await conn.execute(statement)

    # psycopg requires a selector event loop on Windows. This does not alter the
    # application's global event-loop policy or launch another service.
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(probe())
