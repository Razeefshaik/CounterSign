"""M07/M15 dashboard sources: fixtures by default, opt-in read-only PostgreSQL.

COUNTERSIGN_DASHBOARD_SOURCE=database selects the C11 views through a separate
pool. Every transaction is read-only and runs as countersign_agent_ro. The
runtime owns this pool's lifespan; it never shares the executor's connections.
Missing database cash is unavailable, never replaced by fixture/zero money.
"""

import asyncio
import base64
import hashlib
import json
import os
import re
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import UTC, date, datetime
from pathlib import Path
from typing import ClassVar, Protocol
from urllib.parse import urlsplit
from uuid import UUID

import httpx
import psycopg
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

FIXTURES = Path(__file__).resolve().parents[3] / "web" / "fixtures"
LISTS = {
    "incidents": ({"state", "kind"}, "updated_at", True),
    "proposals": ({"incident_id", "state", "verdict", "action"}, "created_at", True),
    "payout-runs": ({"state"}, "week_start", True),
    "makers": (set(), "display_name", False),
    "disputes": ({"phase", "status"}, "updated_at", True),
    "reservations": ({"state", "kind", "dispute_id", "run_id"}, "created_at", True),
    "audit-log": (
        {"proposal_id", "operation_id", "actor", "kind", "since"},
        "id",
        True,
    ),
}


class DashboardSource(Protocol):
    async def list(
        self, resource: str, filters: dict, limit: int, cursor: str | None
    ) -> tuple[list[dict], str | None]: ...
    async def get(self, resource: str, id: str) -> dict | None: ...
    async def singleton(self, resource: str, params: dict) -> dict: ...


def masked(value):
    if isinstance(value, dict):
        return {key: masked(item) for key, item in value.items()}
    if isinstance(value, list):
        return [masked(item) for item in value]
    if isinstance(value, str):
        return re.sub(
            r"(?<![\w*])([A-Za-z0-9._%+-]+)@([A-Za-z0-9.-]+\.[A-Za-z]{2,})",
            lambda m: m[1][0] + "***@" + m[2],
            value,
        )
    return value


class FixtureSource:
    """Contract fixtures. Cursors bind resource, filters, and fixture revision."""

    source_name = "fixtures"

    def __init__(self, directory: Path = FIXTURES, *, runtime=None):
        self.runtime = runtime
        self.docs = {
            path.stem: json.loads(path.read_text(encoding="utf-8"))
            for path in directory.glob("*.json")
        }
        self.docs = masked(self.docs)
        # Contract's run detail example abbreviates its second new-payment line.
        for run in self.docs["run-details"]:
            if len(run["lines"]) < run["line_count"]:
                line = deepcopy(run["lines"][0])
                line.update(
                    id="0192f1c4-7a10-7c3e-9b1a-5d2f00000702",
                    maker={
                        "id": "0192f1c4-7a10-7c3e-9b1a-5d2f00000303",
                        "display_name": "Maker C",
                    },
                    amount={"minor": 30000, "currency": "USD"},
                    obligations=[],
                    attempts=[],
                )
                run["lines"].append(line)
        for maker in self.docs["makers"]["items"]:
            name = maker["display_name"]
            maker["reserved"]["minor"] = {
                "Maker A": 36000,
                "Maker B": 5400,
                "Maker C": 0,
            }[name]
            maker["scheduled"]["minor"] = {
                "Maker A": 0,
                "Maker B": 48600,
                "Maker C": 60000,
            }[name]
        for proposal in self.docs["proposals"]["items"]:
            if proposal["action"] == "SCHEDULE_PAYOUT_RUN":
                run = self.docs["run-details"][0]
                proposal["params"]["lines"] = [
                    {
                        "line_id": line["id"],
                        "maker_id": line["maker"]["id"],
                        "amount": deepcopy(line["amount"]),
                    }
                    for line in run["lines"]
                ]
                proposal["approval_request"]["payload_hash"] = (
                    "sha256:"
                    + hashlib.sha256(
                        json.dumps(
                            proposal["params"], sort_keys=True, separators=(",", ":")
                        ).encode()
                    ).hexdigest()
                )
        reservation = deepcopy(self.docs["reservations"]["items"][0])
        reservation.update(
            id="0192f1c4-7a10-7c3e-9b1a-5d2f00000b02",
            amount={"minor": 5400, "currency": "USD"},
            obligation_id="0192f1c4-7a10-7c3e-9b1a-5d2f00000803",
            dispute_id="0192f1c4-7a10-7c3e-9b1a-5d2f00000102",
            reason="Maker B share of disputed order #1052",
        )
        self.docs["reservations"]["items"].append(reservation)
        graph = self.docs["money-graph"]

        def node(id, type, label, state, amount):
            if not any(n["id"] == id for n in graph["nodes"]):
                graph["nodes"].append(
                    {
                        "id": id,
                        "type": type,
                        "label": label,
                        "state": state,
                        "amount": amount,
                    }
                )

        def edge(source, target, type):
            graph["edges"].append({"from": source, "to": target, "type": type})

        for run in self.docs["run-details"]:
            for line in run["lines"] + run["replacements"]:
                id = line.get("id") or line["line_id"]
                lid, mid = "line:" + id, "maker:" + line["maker"]["id"]
                node(mid, "maker", line["maker"]["display_name"], None, None)
                node(
                    lid,
                    "line",
                    line["maker"]["display_name"] + " payout",
                    line.get("state", "UNPAID"),
                    line["amount"],
                )
                edge(lid, mid, "pays")
                for obligation in line.get("obligations", []):
                    oid = "obligation:" + obligation["id"]
                    node(
                        oid,
                        "obligation",
                        obligation["order_ref"],
                        obligation["state"],
                        obligation["amount"],
                    )
                    edge(oid, lid, "allocated_to")
                for attempt in line.get("attempts", []):
                    aid = "attempt:" + attempt["id"]
                    node(
                        aid,
                        "attempt",
                        "Attempt " + str(attempt["attempt_no"]),
                        attempt["state"],
                        attempt["amount"],
                    )
                    edge(lid, aid, "attempted_by")
        self.revision = hashlib.sha256(json.dumps(self.docs, sort_keys=True).encode()).hexdigest()[
            :16
        ]

    async def list(self, resource, filters, limit, cursor):
        allowed, sort, descending = LISTS[resource]
        if filters.keys() - allowed or not 1 <= limit <= 200:
            raise ValueError("Invalid filter or limit")
        if "since" in filters:
            since = datetime.fromisoformat(filters["since"])
            if since.tzinfo is None:
                raise ValueError("Timezone required")
        rows = deepcopy(self.docs[resource]["items"])
        rows = [
            row
            for row in rows
            if all(
                datetime.fromisoformat(row["at"]) >= datetime.fromisoformat(value)
                if key == "since"
                else row.get(key) == value
                for key, value in filters.items()
            )
        ]
        rows.sort(key=lambda row: (row[sort], str(row["id"])), reverse=descending)
        offset = 0
        if cursor:
            try:
                data = json.loads(base64.urlsafe_b64decode(cursor).decode())
                if (
                    data["resource"] != resource
                    or data["filters"] != filters
                    or data["revision"] != self.revision
                ):
                    raise ValueError("Cursor does not match query")
                offset = data["offset"]
                if type(offset) is not int or offset < 0:
                    raise ValueError("Invalid offset")
            except (ValueError, TypeError, KeyError, UnicodeError):
                raise ValueError("Invalid cursor") from None
        page = rows[offset : offset + limit]
        next_cursor = None
        if offset + limit < len(rows):
            next_cursor = base64.urlsafe_b64encode(
                json.dumps(
                    {
                        "resource": resource,
                        "filters": filters,
                        "revision": self.revision,
                        "offset": offset + limit,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).decode()
        return page, next_cursor

    async def get(self, resource, id):
        if resource == "payout-runs":
            return deepcopy(next((r for r in self.docs["run-details"] if r["id"] == id), None))
        doc = deepcopy(next((r for r in self.docs[resource]["items"] if r["id"] == id), None))
        if doc is not None and resource == "incidents":
            doc["proposals"] = sorted(
                [deepcopy(p) for p in self.docs["proposals"]["items"] if p["incident_id"] == id],
                key=lambda p: p["created_at"],
                reverse=True,
            )
            ids = {p["id"] for p in doc["proposals"]}
            doc["timeline"] = sorted(
                [deepcopy(a) for a in self.docs["audit-log"]["items"] if a["proposal_id"] in ids],
                key=lambda a: a["at"],
            )
        return doc

    async def singleton(self, resource, params):
        if resource != "money-graph":
            if params:
                raise ValueError("Unexpected query parameters")
            doc = deepcopy(self.docs[resource])
            if resource == "meta" and self.runtime is not None:
                doc.update(_mode_metadata(self.runtime))
            return doc
        if len(params) != 1 or not set(params) <= {"dispute_id", "maker_id", "run_id"}:
            raise ValueError("Choose one graph root")
        kind, id = next(iter(params.items()))
        kind = {"dispute_id": "dispute", "maker_id": "maker", "run_id": "run"}[kind]
        doc = deepcopy(self.docs["money-graph"])
        root = kind + ":" + id
        if kind == "run":
            run = next((r for r in self.docs["run-details"] if r["id"] == id), None)
            reachable = (
                {
                    "line:" + (line.get("id") or line["line_id"])
                    for line in run["lines"] + run["replacements"]
                }
                if run
                else {root}
            )
        else:
            reachable = {root}
        while True:
            added = {e["to"] for e in doc["edges"] if e["from"] in reachable} | {
                e["from"] for e in doc["edges"] if e["to"] in reachable
            }
            if added <= reachable:
                break
            reachable |= added
        doc["nodes"] = [node for node in doc["nodes"] if node["id"] in reachable]
        doc["edges"] = [
            edge for edge in doc["edges"] if edge["from"] in reachable and edge["to"] in reachable
        ]
        return doc


class DashboardUnavailable(RuntimeError):
    """Value-free database failure for the public API boundary."""


def _mode_metadata(runtime):
    mock = runtime.paypal.is_mock
    return {
        "mode": runtime.mode,
        "paypal_env": "mock" if mock else "sandbox",
        "simulated": mock,
        "simulation_label": "Simulated: mock PayPal" if mock else None,
    }


async def _demo_clock(runtime):
    try:
        async with httpx.AsyncClient(timeout=3, follow_redirects=False, trust_env=False) as client:
            response = await client.get(runtime.paypal.base_url.rstrip("/") + "/_mock/clock")
            response.raise_for_status()
            now = datetime.fromisoformat(response.json()["now"])
            if now.tzinfo is None:
                raise ValueError("Timezone required")
            return now
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        raise DashboardUnavailable("Demo clock unavailable") from None


class DbSource:
    """Read only C11's allowlisted views; cursors bind source/resource/filters.

    Keyset pagination uses (sort column, id), so a newer insertion cannot shift
    offsets. Maker cursors contain only an anchor ID: a display name could itself
    be an email, which must never leak through reversible cursor encoding. A
    deleted maker anchor invalidates that cursor and asks the caller to reload.
    """

    source_name = "database"
    views: ClassVar[dict[str, str]] = {
        "incidents": "v_dash_incidents",
        "proposals": "v_dash_proposals",
        "payout-runs": "v_dash_runs",
        "makers": "v_dash_makers",
        "disputes": "v_dash_disputes",
        "reservations": "v_dash_reservations",
        "audit-log": "v_dash_audit",
    }
    details: ClassVar[dict[str, str]] = {
        "incidents": "v_dash_incident_detail",
        "proposals": "v_dash_proposals",
        "payout-runs": "v_dash_run_detail",
        "disputes": "v_dash_disputes",
    }

    def __init__(self, database_url, *, runtime, clock=None):
        self.runtime = runtime
        self.clock = clock
        self.pool = AsyncConnectionPool(
            database_url,
            open=False,
            min_size=1,
            max_size=4,
            timeout=5,
            name="dashboard-read-only",
            kwargs={"autocommit": True, "row_factory": dict_row},
            configure=self._configure,
        )

    def __repr__(self):
        return "DbSource(connection=<REDACTED>)"

    @staticmethod
    async def _configure(conn):
        if conn.info.server_version // 10000 != 16:
            raise DashboardUnavailable("Dashboard requires PostgreSQL 16")
        await conn.execute("SET default_transaction_read_only = on")
        await conn.execute("SET default_transaction_isolation = 'repeatable read'")

    @asynccontextmanager
    async def _read(self):
        try:
            async with self.pool.connection() as conn, conn.transaction():
                await conn.execute("SET LOCAL ROLE countersign_agent_ro")
                yield conn
        except psycopg.Error:
            raise DashboardUnavailable("Dashboard database unavailable") from None

    async def __aenter__(self):
        try:
            await self.pool.open(wait=True)
            async with self._read() as conn:
                await conn.execute("SELECT doc FROM v_dash_incidents LIMIT 0")
        except BaseException:
            await self.pool.close()
            raise
        return self

    async def __aexit__(self, *args):
        await self.pool.close()

    @staticmethod
    def _cursor(resource, filters, row):
        value = row["cursor_value"]
        if isinstance(value, date):
            value = value.isoformat()
        doc = {
            "version": 1,
            "source": "database",
            "resource": resource,
            "filters": filters,
            "id": str(row["cursor_id"]),
            "value": None if resource == "makers" else value,
        }
        return base64.urlsafe_b64encode(
            json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()
        ).decode()

    @staticmethod
    def _boundary(cursor, resource, filters):
        if not isinstance(cursor, str) or len(cursor) > 8192:
            raise ValueError("Invalid cursor")
        try:
            doc = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
            if (
                doc["version"] != 1
                or doc["source"] != "database"
                or doc["resource"] != resource
                or doc["filters"] != filters
            ):
                raise ValueError("Cursor does not match query")
            identifier = int(doc["id"]) if resource == "audit-log" else UUID(doc["id"])
            value = doc["value"]
            if resource == "audit-log":
                if type(value) is not int or value != identifier or not 0 < identifier < 2**63:
                    raise ValueError("Invalid audit cursor")
            elif resource == "payout-runs":
                value = date.fromisoformat(value)
            elif resource != "makers":
                value = datetime.fromisoformat(value)
                if value.utcoffset() is None:
                    raise ValueError("Timezone required")
            elif value is not None:
                raise ValueError("Invalid maker cursor")
            return value, identifier
        except (ValueError, TypeError, KeyError, UnicodeError, AttributeError, OverflowError):
            raise ValueError("Invalid cursor") from None

    async def list(self, resource, filters, limit, cursor):
        if resource not in self.views:
            raise ValueError("Invalid resource")
        allowed, sort, descending = LISTS[resource]
        if filters.keys() - allowed or type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("Invalid filters or limit")
        clauses, values = [], []
        for key, value in sorted(filters.items()):
            if not isinstance(value, str):
                raise ValueError("Invalid filter")  # noqa: TRY004 - public query validation uses 400
            if key == "since":
                value = datetime.fromisoformat(value)
                if value.utcoffset() is None:
                    raise ValueError("Timezone required")
                clauses.append(sql.SQL("at >= %s"))
            else:
                clauses.append(sql.SQL("{}::text = %s").format(sql.Identifier(key)))
            values.append(value)
        boundary = self._boundary(cursor, resource, filters) if cursor else None
        async with self._read() as conn:
            if boundary:
                value, identifier = boundary
                if resource == "makers":
                    anchor = await (
                        await conn.execute(
                            "SELECT display_name FROM v_dash_makers WHERE id=%s", (identifier,)
                        )
                    ).fetchone()
                    if anchor is None:
                        raise ValueError("Maker cursor anchor is missing")
                    value = anchor["display_name"]
                clauses.append(
                    sql.SQL("({},id) {} (%s,%s)").format(
                        sql.Identifier(sort), sql.SQL("<" if descending else ">")
                    )
                )
                values.extend([value, identifier])
            query = sql.SQL(
                "SELECT doc, {} AS cursor_value, id AS cursor_id FROM {} WHERE {} ORDER BY {} {}, id {} LIMIT %s"
            ).format(
                sql.Identifier(sort),
                sql.Identifier(self.views[resource]),
                sql.SQL(" AND ").join(clauses) if clauses else sql.SQL("true"),
                sql.Identifier(sort),
                sql.SQL("DESC" if descending else "ASC"),
                sql.SQL("DESC" if descending else "ASC"),
            )
            rows = await (await conn.execute(query, [*values, limit + 1])).fetchall()
        page = rows[:limit]
        next_cursor = self._cursor(resource, filters, page[-1]) if len(rows) > limit else None
        return [self._present(resource, masked(row["doc"])) for row in page], next_cursor

    def _present(self, resource, doc):
        """Every dispute on the local mock was opened through its staged control.

        The contract's `staged` flag marks disputes opened for the demo through the
        sandbox Resolution Center or the mock; the mock does not echo that flag.
        """
        if resource == "disputes" and doc is not None and self.runtime.paypal.is_mock:
            doc["staged"] = True
        return doc

    async def get(self, resource, id):
        if resource not in self.details:
            raise ValueError("Invalid detail resource")
        try:
            identifier = UUID(id)
        except (ValueError, TypeError, AttributeError):
            return None
        async with self._read() as conn:
            row = await (
                await conn.execute(
                    sql.SQL("SELECT doc FROM {} WHERE id=%s").format(
                        sql.Identifier(self.details[resource])
                    ),
                    (identifier,),
                )
            ).fetchone()
        return self._present(resource, masked(row["doc"])) if row else None

    async def singleton(self, resource, params):
        if resource == "money-graph":
            if len(params) != 1 or not set(params) <= {"dispute_id", "maker_id", "run_id"}:
                raise ValueError("Choose one graph root")
            try:
                key, value = next(iter(params.items()))
                identifier = UUID(value)
            except (ValueError, TypeError, AttributeError):
                return {"nodes": [], "edges": []}
            args = [
                identifier if name == key else None for name in ["dispute_id", "maker_id", "run_id"]
            ]
            async with self._read() as conn:
                row = await (
                    await conn.execute(
                        "SELECT dash_money_graph(%s::uuid,%s::uuid,%s::uuid) AS doc", args
                    )
                ).fetchone()
            return masked(row["doc"])
        if params:
            raise ValueError("Unexpected query parameters")
        if resource == "meta":
            now = datetime.now(UTC)
            if self.clock is not None:
                now = self.clock()
            elif self.runtime.mode == "demo" and self.runtime.paypal.is_mock:
                now = await _demo_clock(self.runtime)
            return {
                **_mode_metadata(self.runtime),
                "clock": now.astimezone(UTC).isoformat().replace("+00:00", "Z"),
                "version": "0.1.0",
                "contract_version": "1.0.4",
            }
        if resource != "cash":
            raise ValueError("Invalid singleton resource")
        async with self._read() as conn:
            row = await (await conn.execute("SELECT doc FROM v_dash_cash")).fetchone()
        if row is None:
            raise DashboardUnavailable("Cash snapshot unavailable")
        return masked(row["doc"])


@asynccontextmanager
async def configured_source(runtime, app_state=None):
    """Runtime-only selection; no .env read, no fixture fallback on DB failure."""
    name = os.environ.get("COUNTERSIGN_DASHBOARD_SOURCE", "fixtures")
    if name not in {"fixtures", "database"}:
        raise ValueError("Dashboard source must be fixtures or database")
    if app_state is not None:
        app_state.dashboard_source = name
    if name == "fixtures":
        yield FixtureSource(runtime=runtime)
    else:
        async with DbSource(runtime.database_url, runtime=runtime) as source:
            yield source


def error(status, code, message):
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def build_router(source: DashboardSource | None = None):
    source = source or FixtureSource()
    result = APIRouter()

    def list_endpoint(resource):
        async def endpoint(request: Request):
            params = dict(request.query_params)
            try:
                limit = int(params.pop("limit", "50"))
                cursor = params.pop("cursor", None)
                if not 1 <= limit <= 200:
                    return error(422, "INVALID_REQUEST", "Limit must be between 1 and 200")
                items, cursor = await source.list(resource, params, limit, cursor)
                return {"items": items, "next_cursor": cursor}
            except ValueError:
                return error(400, "INVALID_REQUEST", "Invalid filters, limit or cursor")
            except DashboardUnavailable:
                return error(503, "DATA_UNAVAILABLE", "Dashboard database unavailable")

        return endpoint

    def detail_endpoint(resource):
        async def endpoint(id: str):
            try:
                doc = await source.get(resource, id)
            except DashboardUnavailable:
                return error(503, "DATA_UNAVAILABLE", "Dashboard database unavailable")
            return doc if doc is not None else error(404, "NOT_FOUND", "Resource not found")

        return endpoint

    def singleton_endpoint(resource):
        async def endpoint(request: Request):
            try:
                return await source.singleton(resource, dict(request.query_params))
            except ValueError:
                return error(400, "INVALID_REQUEST", "Invalid singleton query")
            except DashboardUnavailable:
                return error(503, "DATA_UNAVAILABLE", "Dashboard data unavailable")

        return endpoint

    for resource in LISTS:
        result.add_api_route(
            "/" + resource,
            list_endpoint(resource),
            methods=["GET"],
            name="list_" + resource,
        )
    for resource in ["incidents", "proposals", "payout-runs", "disputes"]:
        result.add_api_route(
            "/" + resource + "/{id}",
            detail_endpoint(resource),
            methods=["GET"],
            name="get_" + resource,
        )
    for resource in ["meta", "cash", "money-graph"]:
        result.add_api_route(
            "/" + resource,
            singleton_endpoint(resource),
            methods=["GET"],
            name="get_" + resource,
        )
    return result


router = build_router()


# --- M17 demo control bar -------------------------------------------------
#
# The bar replays C25's scripted mock week (scripts/demo_week.py), the same
# command the hosted container uses to seed it. Each POST runs that command once
# more with --pause-after-step, so one checkpoint step succeeds and the process
# exits. The bar never receives parameters, never calls PayPal or the engine
# itself, and therefore cannot reach the shield or executor any other way than
# the week already does. It exists only in demo mode on the loopback mock.

REPO_ROOT = Path(__file__).resolve().parents[3]
DEMO_STEP_TIMEOUT = 900
_CODE = re.compile(r"[A-Z][A-Z0-9_]{2,80}")
# (checkpoint step, what the presenter sees, whether the step contains a staged action)
DEMO_STEPS = (
    (
        "setup",
        "Monday: stage four buyer orders on the mock; C's old $300 payout is UNCLAIMED",
        True,
    ),
    (
        "seed",
        "Monday: load makers, orders and maker shares; $1,500 available",
        True,
    ),
    (
        "monday_dispute",
        ("Monday: buyer disputes Maker A's $400 order; A's $360 share is reserved and "
        "the evidence path is checked"),
        True,
    ),
    (
        "wednesday_dispute",
        "Wednesday: buyer disputes Maker B's $60 order; B's $54 share is reserved",
        True,
    ),
    (
        "cancel_and_verify",
        ("Thursday: agent cancels C's UNCLAIMED $300 payout and a GET confirms RETURNED; "
        "owner verifies C's payee"),
        True,
    ),
    (
        "friday_plan",
        ("Friday: plan the run; A's $360 and B's $54 stay reserved; the naive comparison "
        "is read only"),
        False,
    ),
    (
        "friday_execute",
        ("Friday: owner approves the exact $1,086 run once; a lost PayPal response is "
        "recovered with the same keys"),
        True,
    ),
    (
        "owner_claim_and_draft",
        ("Friday: agent drafts a note to Maker A; B's $60 claim acceptance waits for the "
        "owner"),
        False,
    ),
    (
        "safety_blocks",
        ("Friday: a buyer note asks for a refund to another account, and a settled line "
        "is retried; both blocked"),
        True,
    ),
    (
        "following_week_settle",
        ("Following week: A's dispute is adjudicated for the seller; the reconciler "
        "releases A's reserve"),
        True,
    ),
    (
        "following_week_pay_a",
        ("Following week: owner approves A's $360 only after the release; $354 "
        "available"),
        True,
    ),
)
DEMO_STAGED_NOTE = (
    "Staged: buyer actions, dispute settlement, the lost response and owner clicks are "
    "simulated on the mock. The agent is a scripted model; no LLM or PayPal call is made."
)


def demo_bar_allowed(runtime) -> bool:
    """Demo mode on the loopback mock with a disposable database name, never live."""
    try:
        paypal = runtime.paypal
        name = conninfo_to_dict(runtime.database_url).get("dbname") or ""
        host = urlsplit(paypal.base_url).hostname
    except Exception:  # noqa: BLE001 - a private DSN never reaches an error message.
        return False
    return (
        runtime.mode == "demo"
        and paypal.is_mock is True
        and host in {"127.0.0.1", "localhost"}
        and name.endswith(("_demo", "_test"))
    )


async def _run_week_command(arguments, timeout=DEMO_STEP_TIMEOUT):
    """Run the staged week CLI once; return (exit code, its last fixed code or None).

    Its notes are redacted by the CLI but still discarded here: only a fixed
    upper-case code from the final line reaches the API.
    """
    import subprocess
    import sys

    def run():
        try:
            completed = subprocess.run(
                [sys.executable, "-m", "scripts.demo_week", *arguments],
                cwd=REPO_ROOT,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return 124, "DEMO_STEP_TIMEOUT"
        lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
        last = lines[-1] if lines else ""
        return completed.returncode, last if _CODE.fullmatch(last) else None

    return await asyncio.to_thread(run)


class DemoBar:
    """Steps or restarts the staged mock week; one command at a time."""

    def __init__(self, runtime, root, app_url, *, runner=_run_week_command, today=None):
        if not demo_bar_allowed(runtime):
            raise ValueError("The demo bar runs only in demo mode on the local mock")
        self.runtime = runtime
        self.base = Path(root)
        self.app_url = app_url
        self.runner = runner
        self.today = today or (lambda: datetime.now(UTC).date())
        self.task = None
        self.action = None
        self.last = None
        # Earlier resets leave sibling roots behind; continue after the newest.
        resets = [
            int(path.name.rsplit("-", 1)[1])
            for path in self.base.parent.glob(self.base.name + ".reset-*")
            if path.name.rsplit("-", 1)[1].isdigit()
        ]
        self.generation = max(resets, default=0)

    @property
    def root(self):
        if not self.generation:
            return self.base
        return self.base.with_name(f"{self.base.name}.reset-{self.generation}")

    @property
    def running(self):
        return self.task is not None and not self.task.done()

    def _checkpoint(self):
        """The newest saved run under the current root: (date, steps, data)."""
        runs = (
            sorted(
                path
                for path in self.root.iterdir()
                if path.is_dir() and re.fullmatch(r"\d{4}-\d{2}-\d{2}", path.name)
            )
            if self.root.is_dir()
            else []
        )
        if not runs:
            return None, {}, {}
        try:
            state = json.loads((runs[-1] / "state.json").read_text(encoding="utf-8"))
            steps, data = state.get("steps", {}), state.get("data", {})
            if not isinstance(steps, dict) or not isinstance(data, dict):
                raise TypeError
        except (OSError, ValueError, TypeError):
            return runs[-1].name, {}, {}
        return runs[-1].name, steps, data

    def state(self):
        _, saved, data = self._checkpoint()
        done = [saved.get(name) == "SUCCEEDED" for name, _, _ in DEMO_STEPS]
        step = sum(done)
        stopped = [name for name, _, _ in DEMO_STEPS if saved.get(name) == "IN_PROGRESS"]
        clock = data.get("mock_now") or data.get("monday")
        upcoming = next(
            (label for (_, label, _), finished in zip(DEMO_STEPS, done, strict=True) if not finished),
            None,
        )
        mock = self.runtime.paypal.is_mock
        return {
            "scenario": "demo_week",
            "step": step,
            "total": len(DEMO_STEPS),
            "complete": all(done),
            "replaying": True,
            "running": self.running,
            "action": self.action if self.running else None,
            "simulated": mock,
            "simulation_label": "Simulated: mock PayPal" if mock else None,
            "model": "scripted",
            "staged_note": DEMO_STAGED_NOTE,
            "clock": clock if isinstance(clock, str) and _time_text(clock) else None,
            "steps": [
                {
                    "index": index,
                    "name": name,
                    "label": label,
                    "staged": staged,
                    "done": finished,
                    "interrupted": name in stopped and not self.running,
                }
                for index, ((name, label, staged), finished) in enumerate(
                    zip(DEMO_STEPS, done, strict=True), 1
                )
            ],
            "next_label": upcoming,
            "last_result": self.last,
        }

    async def _execute(self, action, arguments):
        try:
            code, last = await self.runner(arguments)
        except Exception:  # noqa: BLE001 - subprocess failures stay a fixed code.
            code, last = 1, None
        ok = code == 0
        if action == "reset" and ok:
            self.generation += 1
        self.last = {
            "action": action,
            "ok": ok,
            "code": last or ("DEMO_" + action.upper() + ("_DONE" if ok else "_FAILED")),
        }

    def _common(self):
        return ["--target", "mock", "--model", "scripted", "--app-url", self.app_url]

    def start(self, action):
        """Start one step or reset in the background; returns an error code or None."""
        if not demo_bar_allowed(self.runtime):
            return "DEMO_DISABLED"
        if self.running:
            return "DEMO_BUSY"
        if action == "step":
            date, _, _ = self._checkpoint()
            if self.state()["complete"]:
                return "DEMO_WEEK_COMPLETE"
            arguments = [
                *self._common(),
                "--root", str(self.root),
                "--date", date or self.today().isoformat(),
                "--pause-after-step",
            ]
        else:
            arguments = [*self._common(), "--root", str(self.root), "--reset-mock-week"]
        self.action = action
        self.task = asyncio.get_running_loop().create_task(self._execute(action, arguments))
        return None

    async def wait(self):
        if self.task is not None:
            await self.task


def _time_text(value):
    try:
        return datetime.fromisoformat(value).utcoffset() is not None
    except ValueError:
        return False


def configured_demo_bar(runtime):
    """COUNTERSIGN_DEMO_ROOT/APP_URL enable the bar; live or sandbox never does."""
    root = os.environ.get("COUNTERSIGN_DEMO_ROOT", "").strip()
    app_url = os.environ.get("COUNTERSIGN_DEMO_APP_URL", "").strip()
    if not root or not re.fullmatch(r"http://(127\.0\.0\.1|localhost):\d{1,5}", app_url):
        return None
    if not demo_bar_allowed(runtime):
        return None
    return DemoBar(runtime, root, app_url)


def build_demo_router(bar: DemoBar):
    """GET state, POST step, POST reset. Bodies are ignored; nothing is parameterised."""
    result = APIRouter()

    def refused():
        return error(404, "NOT_FOUND", "Resource not found")

    @result.get("/demo/state", name="demo_state")
    async def demo_state():
        if not demo_bar_allowed(bar.runtime):
            return refused()
        return bar.state()

    async def begin(action):
        if not demo_bar_allowed(bar.runtime):
            return refused()
        problem = bar.start(action)
        if problem == "DEMO_DISABLED":
            return refused()
        if problem:
            messages = {
                "DEMO_BUSY": "A staged step is already running",
                "DEMO_WEEK_COMPLETE": "The staged week is complete; reset to replay it",
            }
            return error(409, problem, messages[problem])
        return JSONResponse(status_code=202, content=bar.state())

    @result.post("/demo/step", name="demo_step")
    async def demo_step():
        return await begin("step")

    @result.post("/demo/reset", name="demo_reset")
    async def demo_reset():
        return await begin("reset")

    return result
