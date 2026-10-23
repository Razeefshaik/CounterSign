"""Small repositories over the fixed PLAN 2.2 tables; values are always parameters.

Repositories participate in their caller's transaction. They never open a transaction,
commit independently, or accept free SQL, table names, column expressions or ordering.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from psycopg import sql
from psycopg.types.json import Jsonb

from .ids import uuid7
from .pool import Connection
from .transitions import StaleRow


@dataclass(frozen=True)
class TableSpec:
    columns: frozenset[str]
    key: tuple[str, ...] = ("id",)
    json_columns: frozenset[str] = frozenset()
    generated: frozenset[str] = frozenset()
    uuid_id: bool = True
    append_only: bool = False


def _spec(columns, *, key=("id",), json="", generated="", uuid_id=True, append_only=False):
    return TableSpec(
        frozenset(columns.split()),
        key,
        frozenset(json.split()),
        frozenset(generated.split()),
        uuid_id,
        append_only,
    )


TABLES = MappingProxyType(
    {
        "makers": _spec("id display_name payout_email payout_email_verified_at version created_at"),
        "orders": _spec(
            "id maker_id buyer_ref amount_minor currency paypal_order_id paypal_capture_id "
            "captured_at tracking version",
            json="tracking",
        ),
        "obligations": _spec(
            "id maker_id order_id kind amount_minor currency due_date state version updated_at"
        ),
        "payout_runs": _spec("id week_start state approval_id version"),
        "payout_lines": _spec("id run_id maker_id amount_minor currency state version"),
        "payout_line_obligations": _spec(
            "line_id obligation_id amount_minor active",
            key=("line_id", "obligation_id"),
            uuid_id=False,
        ),
        "operations": _spec(
            "id kind target_type target_id payload payload_hash proposal_id approval_id state response "
            "paypal_debug_id error created_at submitted_at completed_at version",
            json="payload response",
        ),
        "payout_attempts": _spec(
            "id line_id attempt_no operation_id sender_batch_id request_id sender_item_id payee_email "
            "amount_minor currency state paypal_batch_id paypal_item_id paypal_raw prepared_at "
            "last_checked_at version",
            json="paypal_raw",
        ),
        "disputes": _spec(
            "id paypal_dispute_id order_id status lifecycle_stage reason amount_minor currency "
            "seller_response_due_date outcome_code amount_refunded_minor phase links raw last_synced_at "
            "version",
            json="links raw",
        ),
        "reservations": _spec(
            "id kind affects_cash obligation_id dispute_id proposal_id run_id amount_minor currency "
            "state created_at closed_at",
            generated="affects_cash",
        ),
        "ledger_journals": _spec("id operation_id occurred_at paypal_effective_at memo"),
        "ledger_postings": _spec(
            "journal_id account amount_minor currency", key=(), uuid_id=False, append_only=True
        ),
        "incidents": _spec("id kind subject state opened_at closed_at version", json="subject"),
        "proposals": _spec(
            "id incident_id action params evidence_refs rationale expected_effect model raw_output "
            "verdict verdict_rules verdict_details state created_at version",
            json="params evidence_refs expected_effect raw_output verdict_details",
        ),
        "approvals": _spec(
            "id proposal_id payload_hash record_versions approved_by approved_at expires_at used_at "
            "used_by_operation_id",
            json="record_versions",
        ),
        "webhook_inbox": _spec(
            "event_id event_type resource_type resource_id headers raw_body payload paypal_create_time "
            "received_at signature status processed_at attempts error",
            key=("event_id",),
            json="headers payload",
            uuid_id=False,
        ),
        "balance_snapshots": _spec(
            "id fetched_at as_of_time last_refresh_time currency available_minor withheld_minor "
            "total_minor raw",
            json="raw",
        ),
        "policies": _spec(
            "key value version updated_at", key=("key",), json="value", uuid_id=False
        ),
        "audit_log": _spec(
            "id at actor kind proposal_id operation_id rule verdict details",
            json="details",
            generated="id",
            uuid_id=False,
            append_only=True,
        ),
    }
)


class Repository:
    """Use `Repository(conn, 'makers')` or `Repositories(conn).makers`. Rows are dictionaries."""

    def __init__(self, conn: Connection, table: str):
        if table not in TABLES:
            raise ValueError("Unknown repository table")
        self.conn = conn
        self.table = table
        self.spec = TABLES[table]

    def _check_columns(self, values: Mapping[str, Any], *, writing=False) -> None:
        if set(values) - self.spec.columns:
            raise ValueError("Unknown repository column")
        if writing and set(values) & self.spec.generated:
            raise ValueError("Generated columns cannot be written")
        for column, value in values.items():
            if column.endswith("_minor") and value is not None and type(value) is not int:
                raise TypeError("Money must use integer minor units")

    def _adapt(self, column: str, value: Any):
        return Jsonb(value) if column in self.spec.json_columns and value is not None else value

    def _where(self, filters: Mapping[str, Any]) -> tuple[sql.Composed, list[Any]]:
        self._check_columns(filters)
        terms, values = [], []
        for column, value in filters.items():
            if value is None:
                terms.append(sql.SQL("{} IS NULL").format(sql.Identifier(column)))
            else:
                terms.append(sql.SQL("{} = %s").format(sql.Identifier(column)))
                values.append(self._adapt(column, value))
        return sql.SQL(" AND ").join(terms), values

    def _key(self, key) -> dict:
        if not self.spec.key:
            raise ValueError("This append-only table has no row key; use find()")
        values = key if isinstance(key, tuple) else (key,)
        if len(values) != len(self.spec.key) or any(value is None for value in values):
            raise ValueError("Wrong repository key shape")
        return dict(zip(self.spec.key, values, strict=True))

    async def insert(self, **values) -> dict:
        self._check_columns(values, writing=True)
        if self.spec.uuid_id and "id" not in values:
            values["id"] = uuid7()
        columns = list(values)
        query = (
            sql.SQL("INSERT INTO {} ({}) VALUES ({}) RETURNING *").format(
                sql.Identifier(self.table),
                sql.SQL(", ").join(map(sql.Identifier, columns)),
                sql.SQL(", ").join(sql.Placeholder() for _ in columns),
            )
            if columns
            else sql.SQL("INSERT INTO {} DEFAULT VALUES RETURNING *").format(
                sql.Identifier(self.table)
            )
        )
        cursor = await self.conn.execute(
            query, [self._adapt(column, values[column]) for column in columns]
        )
        return await cursor.fetchone()

    async def get(self, key, *, for_update: bool = False) -> dict | None:
        where, params = self._where(self._key(key))
        query = sql.SQL("SELECT * FROM {} WHERE {}{}").format(
            sql.Identifier(self.table), where, sql.SQL(" FOR UPDATE" if for_update else "")
        )
        cursor = await self.conn.execute(query, params)
        return await cursor.fetchone()

    async def find(
        self, filters: Mapping[str, Any] | None = None, *, limit: int = 100
    ) -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("limit must be an integer from 1 to 1000")
        where, params = self._where(filters or {})
        ordering = self.spec.key or ("journal_id", "account", "currency", "amount_minor")
        query = sql.SQL("SELECT * FROM {}{} ORDER BY {} LIMIT %s").format(
            sql.Identifier(self.table),
            sql.SQL(" WHERE {} ").format(where) if filters else sql.SQL(""),
            sql.SQL(", ").join(map(sql.Identifier, ordering)),
        )
        cursor = await self.conn.execute(query, [*params, limit])
        return await cursor.fetchall()

    async def update(self, key, *, expected_version: int | None = None, **values) -> dict | None:
        if self.spec.append_only:
            raise ValueError("This repository is append-only")
        self._check_columns(values, writing=True)
        if not values or set(values) & {*self.spec.key, "version"}:
            raise ValueError("Updates require values and cannot replace keys or version")
        filters = self._key(key)
        if expected_version is not None:
            if "version" not in self.spec.columns:
                raise ValueError("This table does not have a version column")
            if type(expected_version) is not int or expected_version < 1:
                raise ValueError("expected_version must be a positive integer")
            filters["version"] = expected_version
        where, params = self._where(filters)
        assignments = [sql.SQL("{} = %s").format(sql.Identifier(column)) for column in values]
        if "version" in self.spec.columns:
            assignments.append(sql.SQL("version = version + 1"))
        if "updated_at" in self.spec.columns and "updated_at" not in values:
            assignments.append(sql.SQL("updated_at = now()"))
        query = sql.SQL("UPDATE {} SET {} WHERE {} RETURNING *").format(
            sql.Identifier(self.table), sql.SQL(", ").join(assignments), where
        )
        cursor = await self.conn.execute(
            query, [*[self._adapt(column, value) for column, value in values.items()], *params]
        )
        row = await cursor.fetchone()
        if row is None and expected_version is not None:
            raise StaleRow("Version changed, or the row no longer exists")
        return row

    async def delete(self, key) -> bool:
        if self.spec.append_only:
            raise ValueError("This repository is append-only")
        where, params = self._where(self._key(key))
        cursor = await self.conn.execute(
            sql.SQL("DELETE FROM {} WHERE {} RETURNING 1").format(
                sql.Identifier(self.table), where
            ),
            params,
        )
        return await cursor.fetchone() is not None


class Repositories:
    """One fixed repository for each slice table, sharing the caller's connection."""

    def __init__(self, conn: Connection):
        self._repositories = {name: Repository(conn, name) for name in TABLES}

    def __getitem__(self, name: str) -> Repository:
        return self._repositories[name]

    def __getattr__(self, name: str) -> Repository:
        try:
            return self._repositories[name]
        except KeyError:
            raise AttributeError(name) from None
