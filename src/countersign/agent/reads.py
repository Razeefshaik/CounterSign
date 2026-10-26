"""The commander's three custom read tools; none has a money-write capability.

The database connection is deliberately independent of ``countersign.db``. Each
query runs in a new read-only transaction with the restricted agent role, and
only fixed dashboard-view statements can be selected by model input.
"""

import re
from collections.abc import Mapping
from contextlib import asynccontextmanager
from typing import Protocol
from uuid import UUID

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from countersign.agent.safety import sanitize
from countersign.agent.tools import ReadTool, ReadToolError

AGENT_ROLE = "countersign_agent_ro"
_SCHEMA = re.compile(r"[a-z_][a-z0-9_]{0,62}\Z")
_PAYPAL_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_VIEWS = (
    "v_dash_audit",
    "v_dash_proposals",
    "v_dash_incidents",
    "v_dash_incident_detail",
    "v_dash_reservations",
    "v_dash_makers",
    "v_dash_disputes",
    "v_dash_runs",
    "v_dash_run_detail",
    "v_dash_cash",
)

# The model selects a name, never an identifier, expression, WHERE clause or SQL.
_QUERIES = {
    "incident": ("v_dash_incident_detail", "id", "id", False),
    "dispute": ("v_dash_disputes", "id", "id", False),
    "maker": ("v_dash_makers", "id", "id", False),
    "run": ("v_dash_run_detail", "id", "id", False),
    "cash": ("v_dash_cash", None, "currency", False),
    "reservations_by_dispute": ("v_dash_reservations", "dispute_id", "id", True),
    "reservations_by_run": ("v_dash_reservations", "run_id", "id", True),
    "proposals_by_incident": ("v_dash_proposals", "incident_id", "id", True),
}

# has_table_privilege checks inherited grants too. NOINHERIT and no memberships
# also prevent a future role grant from silently broadening this capability.
_ROLE_CHECK = """
SELECT r.rolname AS role,
       NOT (r.rolsuper OR r.rolcreatedb OR r.rolcreaterole OR r.rolinherit
            OR r.rolreplication OR r.rolbypassrls) AS limited_role,
       current_setting('transaction_read_only') = 'on' AS read_only,
       current_setting('server_version_num')::int / 10000 = 16 AS postgres_16,
       NOT has_schema_privilege(current_user, %s, 'CREATE') AS no_schema_create,
       NOT EXISTS (SELECT 1 FROM pg_auth_members WHERE member = r.oid) AS no_memberships,
       NOT EXISTS (
           SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
           WHERE n.nspname = %s AND c.relkind IN ('r','p','v','m','f')
             AND (has_table_privilege(current_user, c.oid,
                      'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
                  OR (c.relkind <> 'v' AND has_table_privilege(current_user, c.oid, 'SELECT'))
                  OR (c.relkind = 'v' AND NOT c.relname = ANY(%s)
                      AND has_table_privilege(current_user, c.oid, 'SELECT')))
       ) AS view_select_only
FROM pg_roles r WHERE r.rolname = current_user
"""


def _arguments(value, allowed, required=()):
    if not isinstance(value, dict) or set(value) - set(allowed) or set(required) - set(value):
        raise ReadToolError("INVALID_READ_TOOL_ARGUMENTS")


def _uuid(value):
    if not isinstance(value, str):
        raise ReadToolError("INVALID_GRAPH_IDENTIFIER")
    try:
        return UUID(value)
    except (ValueError, AttributeError):
        raise ReadToolError("INVALID_GRAPH_IDENTIFIER") from None


def _simulation_data(value):
    """Simulator money uses integer minor units; a float or bool is never money."""
    if isinstance(value, dict):
        for key, inner in value.items():
            if (key == "minor" or key.endswith("_minor")) and type(inner) is not int:
                raise ReadToolError("INVALID_SIMULATION_RESPONSE")
            if key == "currency" and (
                not isinstance(inner, str) or not re.fullmatch("[A-Z]{3}", inner)
            ):
                raise ReadToolError("INVALID_SIMULATION_RESPONSE")
            _simulation_data(inner)
    elif isinstance(value, list):
        for inner in value:
            _simulation_data(inner)


class MoneyGraphQueries:
    """Execute fixed masked-view queries as the SELECT-only PostgreSQL role.

    ``schema`` is trusted application configuration, never a tool argument. The
    connecting login needs permission to SET ROLE countersign_agent_ro (the role
    itself is NOLOGIN). Connections are short-lived and always closed here.
    ``connect`` exists for offline tests and must be supplied only by trusted code.
    """

    def __init__(self, database_url: str, schema: str = "public", *, connect=None):
        if not isinstance(database_url, str) or not database_url.strip():
            raise ReadToolError("MONEY_GRAPH_DATABASE_REQUIRED")
        if not isinstance(schema, str) or not _SCHEMA.fullmatch(schema):
            raise ReadToolError("INVALID_GRAPH_SCHEMA")
        if schema in {"pg_catalog", "information_schema"}:
            raise ReadToolError("INVALID_GRAPH_SCHEMA")
        self._database_url = database_url
        self._schema = schema
        self._connect = connect if connect is not None else psycopg.AsyncConnection.connect

    def __repr__(self):
        return "MoneyGraphQueries(values=<REDACTED>)"

    @asynccontextmanager
    async def _restricted(self):
        try:
            async with (
                await self._connect(
                    self._database_url, autocommit=True, row_factory=dict_row
                ) as conn,
                conn.transaction(),
            ):
                await conn.execute("SET TRANSACTION READ ONLY")
                await conn.execute("SET LOCAL search_path = pg_catalog")
                await conn.execute("SET LOCAL ROLE countersign_agent_ro")
                cursor = await conn.execute(_ROLE_CHECK, (self._schema, self._schema, list(_VIEWS)))
                record = await cursor.fetchone()
                checks = (
                    "limited_role",
                    "read_only",
                    "postgres_16",
                    "no_schema_create",
                    "no_memberships",
                    "view_select_only",
                )
                if (
                    not isinstance(record, Mapping)
                    or record.get("role") != AGENT_ROLE
                    or any(record.get(key) is not True for key in checks)
                ):
                    raise ReadToolError("MONEY_GRAPH_ROLE_REFUSED")
                yield conn
        except ReadToolError:
            raise
        except Exception:  # noqa: BLE001 - database exceptions can contain private DSNs.
            raise ReadToolError("MONEY_GRAPH_UNAVAILABLE") from None

    async def start(self):
        """Fail startup before any model call if the effective role is unsafe."""
        async with self._restricted():
            pass
        return self

    async def query(self, arguments: dict):
        _arguments(arguments, {"query", "id", "limit"}, {"query"})
        query = arguments["query"]
        if not isinstance(query, str) or query not in _QUERIES:
            raise ReadToolError("UNKNOWN_MONEY_GRAPH_QUERY")
        view, filter_column, sort_column, multiple = _QUERIES[query]
        limit = arguments.get("limit", 20 if multiple else 1)
        if type(limit) is not int or not 1 <= limit <= 100 or (not multiple and limit != 1):
            raise ReadToolError("INVALID_GRAPH_LIMIT")
        if filter_column is None:
            if "id" in arguments:
                raise ReadToolError("INVALID_READ_TOOL_ARGUMENTS")
            condition, params = sql.SQL(""), ()
        else:
            identifier = _uuid(arguments.get("id"))
            condition = sql.SQL(" WHERE {} = %s").format(sql.Identifier(filter_column))
            params = (identifier,)
        statement = sql.SQL("SELECT doc FROM {}{} ORDER BY {} LIMIT %s").format(
            sql.Identifier(self._schema, view), condition, sql.Identifier(sort_column)
        )
        async with self._restricted() as conn:
            cursor = await conn.execute(statement, (*params, limit))
            rows = await cursor.fetchall()
        if not isinstance(rows, list) or any(
            not isinstance(row, Mapping) or not isinstance(row.get("doc"), dict) for row in rows
        ):
            raise ReadToolError("INVALID_MONEY_GRAPH_RESPONSE")
        try:
            return sanitize({"query": query, "items": [row["doc"] for row in rows]})
        except Exception:  # noqa: BLE001 - sanitizer errors must not reveal a database value.
            raise ReadToolError("INVALID_MONEY_GRAPH_RESPONSE") from None


class Simulator(Protocol):
    """A side-effect-free C24 implementation, injected by trusted application code."""

    async def simulate(self, request: dict) -> dict: ...


class CustomReads:
    """Own callbacks for the three tools added to the toolkit's five readers."""

    def __init__(self, reader, money_graph: MoneyGraphQueries, simulator: Simulator | None = None):
        self._reader, self._money_graph, self._simulator = reader, money_graph, simulator

    def __repr__(self):
        return "CustomReads(values=<REDACTED>)"

    async def get_payout_item(self, arguments: dict):
        _arguments(arguments, {"payout_item_id"}, {"payout_item_id"})
        identifier = arguments["payout_item_id"]
        if not isinstance(identifier, str) or not _PAYPAL_ID.fullmatch(identifier):
            raise ReadToolError("INVALID_PAYOUT_ITEM_IDENTIFIER")
        try:
            response = await self._reader.get_payout_item(identifier)
            status = response.status_code
            if type(status) is not int or not 100 <= status <= 599:
                raise ReadToolError("INVALID_PAYOUT_ITEM_RESPONSE")
            if not 200 <= status < 300:
                return {
                    "available": False,
                    "reason": "PAYOUT_ITEM_UNAVAILABLE",
                    "status_code": status,
                }
            # The HTTP adapter masks credential values even when echoed under an
            # unrelated key. Never fall back to its private, unredacted body.
            if not isinstance(response.safe_body, dict):
                raise ReadToolError("UNSAFE_PAYOUT_ITEM_RESPONSE")
            if response.safe_body.get("payout_item_id") != identifier:
                raise ReadToolError("INVALID_PAYOUT_ITEM_RESPONSE")
            return sanitize({"available": True, "payout_item": response.safe_body})
        except ReadToolError:
            raise
        except Exception:  # noqa: BLE001 - a transport error can echo the requested URL or header.
            raise ReadToolError("PAYOUT_ITEM_UNAVAILABLE") from None

    async def query_money_graph(self, arguments: dict):
        return await self._money_graph.query(arguments)

    async def simulate(self, arguments: dict):
        _arguments(arguments, {"run_id", "respect_reserves"}, {"run_id"})
        run_id = _uuid(arguments["run_id"])
        respect = arguments.get("respect_reserves", True)
        if type(respect) is not bool:
            raise ReadToolError("INVALID_SIMULATION_ARGUMENTS")
        if self._simulator is None:
            return {"available": False, "reason": "SIMULATOR_UNAVAILABLE"}
        try:
            result = await self._simulator.simulate(
                {"run_id": str(run_id), "respect_reserves": respect}
            )
            if not isinstance(result, dict):
                raise ReadToolError("INVALID_SIMULATION_RESPONSE")
            _simulation_data(result)
            return sanitize({"available": True, "simulation": result})
        except ReadToolError:
            raise
        except Exception:  # noqa: BLE001 - external simulator errors are not model context.
            raise ReadToolError("SIMULATION_UNAVAILABLE") from None


def custom_tools(reader, money_graph: MoneyGraphQueries, simulator: Simulator | None = None):
    """Build exactly our three read-only tools; registry startup checks all eight."""
    reads = CustomReads(reader, money_graph, simulator)
    return (
        ReadTool(
            "get_payout_item",
            "Read a PayPal payout item by its resource ID; cannot cancel or send a payout.",
            {
                "type": "object",
                "properties": {"payout_item_id": {"type": "string"}},
                "required": ["payout_item_id"],
                "additionalProperties": False,
            },
            reads.get_payout_item,
        ),
        ReadTool(
            "query_money_graph",
            "Read related masked dashboard facts using a fixed query name and internal UUID. "
            "cash needs no id; other queries require id. No SQL or URLs are accepted.",
            {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "enum": list(_QUERIES)},
                    "id": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            reads.query_money_graph,
        ),
        ReadTool(
            "simulate",
            "Ask the read-only what-if simulator about a payout run. An unavailable result "
            "means no prediction was made; simulation never executes a payout or changes reserves.",
            {
                "type": "object",
                "properties": {
                    "run_id": {"type": "string"},
                    "respect_reserves": {"type": "boolean"},
                },
                "required": ["run_id"],
                "additionalProperties": False,
            },
            reads.simulate,
        ),
    )
