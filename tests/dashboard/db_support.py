"""Real C11 schema/data for M15; fake identities only, no environment-file access."""

import os
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from sqlalchemy import create_engine

ROOT = Path(__file__).resolve().parents[2]


@contextmanager
def database():
    base = os.environ.get("COUNTERSIGN_TEST_DATABASE_URL")
    if not base:
        pytest.skip("COUNTERSIGN_TEST_DATABASE_URL is unset")
    schema = "cs_dashboard_" + uuid4().hex
    conninfo = make_conninfo(base, options=f"-csearch_path={schema},public")
    with psycopg.connect(base, autocommit=True) as conn:
        assert conn.info.server_version // 10000 == 16
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        engine = create_engine("postgresql+psycopg://", creator=lambda: psycopg.connect(conninfo))
        try:
            with engine.begin() as connection:
                config = Config(str(ROOT / "migrations/alembic.ini"))
                config.attributes.update(connection=connection, version_table_schema=schema)
                command.upgrade(config, "head")
        finally:
            engine.dispose()
        with psycopg.connect(conninfo, autocommit=True, row_factory=dict_row) as conn:
            ids = seed(conn)
        yield SimpleNamespace(conninfo=conninfo, schema=schema, ids=ids)
    finally:
        assert schema.startswith("cs_dashboard_")
        with psycopg.connect(base, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def seed(conn):
    ids = {
        name: UUID(f"019a12de-7598-7000-8000-{index:012x}")
        for index, name in enumerate(
            [
                "maker",
                "maker2",
                "maker3",
                "order",
                "dispute",
                "incident",
                "incident2",
                "run",
                "line",
                "obligation",
                "proposal",
                "operation",
                "attempt",
                "reservation",
                "snapshot",
            ],
            1,
        )
    }

    def insert(table, **values):
        conn.execute(
            sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                sql.Identifier(table),
                sql.SQL(",").join(map(sql.Identifier, values)),
                sql.SQL(",").join(sql.Placeholder() for _ in values),
            ),
            tuple(
                Jsonb(value) if isinstance(value, (dict, list)) else value
                for value in values.values()
            ),
        )

    now = conn.execute("SELECT now() AS at").fetchone()["at"]
    for key, name in [
        ("maker", "Maker A"),
        ("maker2", "Maker B"),
        ("maker3", "private@example.test"),
    ]:
        insert(
            "makers",
            id=ids[key],
            display_name=name,
            payout_email="maker@example.test",
            payout_email_verified_at=now,
        )
    insert(
        "orders",
        id=ids["order"],
        maker_id=ids["maker"],
        buyer_ref="fake-buyer",
        amount_minor=40000,
        currency="USD",
        paypal_order_id="MOCK-ORDER",
        paypal_capture_id="MOCK-CAPTURE",
        tracking={"order_ref": "#1042"},
    )
    insert(
        "obligations",
        id=ids["obligation"],
        maker_id=ids["maker"],
        order_id=ids["order"],
        kind="ORDER_SHARE",
        amount_minor=36000,
        currency="USD",
        due_date="2026-11-06",
        state="SCHEDULED",
    )
    insert("payout_runs", id=ids["run"], week_start="2026-11-02", state="AWAITING_OWNER")
    insert(
        "payout_lines",
        id=ids["line"],
        run_id=ids["run"],
        maker_id=ids["maker"],
        amount_minor=36000,
        currency="USD",
        state="PAYING",
    )
    insert(
        "payout_line_obligations",
        line_id=ids["line"],
        obligation_id=ids["obligation"],
        amount_minor=36000,
    )
    insert(
        "disputes",
        id=ids["dispute"],
        paypal_dispute_id="PP-D-TEST",
        order_id=ids["order"],
        status="WAITING_FOR_SELLER_RESPONSE",
        lifecycle_stage="CHARGEBACK",
        phase="ACTIVE",
        reason="MERCHANDISE_OR_SERVICE_NOT_RECEIVED",
        amount_minor=40000,
        currency="USD",
        seller_response_due_date=now,
        last_synced_at=now,
        links=[{"rel": "provide_evidence"}],
        raw={"staged": True, "hold_observed": False},
    )
    for key in ["incident", "incident2"]:
        insert(
            "incidents",
            id=ids[key],
            kind="DISPUTE",
            state="AWAITING_OWNER",
            opened_at=now,
            subject={
                "title": "A real database incident",
                "dispute_id": str(ids["dispute"]),
                "order_id": str(ids["order"]),
                "maker_id": str(ids["maker"]),
                "amount_at_risk": {"minor": 40000, "currency": "USD"},
            },
        )
    insert(
        "proposals",
        id=ids["proposal"],
        incident_id=ids["incident"],
        action="SCHEDULE_PAYOUT_RUN",
        params={
            "run_id": str(ids["run"]),
            "lines": [
                {
                    "line_id": str(ids["line"]),
                    "maker_id": str(ids["maker"]),
                    "amount": {"minor": 36000, "currency": "USD"},
                }
            ],
            "replacements": [],
        },
        evidence_refs=[],
        rationale="Pay the approved maker share",
        model="test-model",
        expected_effect={"available_change": {"minor": -36000, "currency": "USD"}},
        verdict="NEEDS_OWNER",
        verdict_details=[{"rule": "FUNDING", "outcome": "PASS", "detail": "Covered"}],
        state="NEEDS_OWNER",
        raw_output={
            "flags": [],
            "approval_request": {
                "payload_hash": "sha256:" + "ab" * 32,
                "record_versions": {"maker:any-key": 1},
                "expires_at": "2026-11-06T17:00:00Z",
            },
        },
    )
    insert(
        "operations",
        id=ids["operation"],
        kind="PAYOUT_CREATE",
        target_type="line",
        target_id=ids["line"],
        payload={},
        payload_hash=b"fake",
        state="SUCCEEDED",
        proposal_id=ids["proposal"],
        response={"batch_header": {"payout_batch_id": "MOCK-BATCH"}},
    )
    insert(
        "payout_attempts",
        id=ids["attempt"],
        line_id=ids["line"],
        attempt_no=1,
        operation_id=ids["operation"],
        sender_batch_id="fake-batch",
        request_id="fake-request",
        sender_item_id="fake-item",
        payee_email="maker@example.test",
        amount_minor=36000,
        currency="USD",
        state="PENDING",
        last_checked_at=now,
    )
    insert(
        "reservations",
        id=ids["reservation"],
        kind="DISPUTE_EARMARK",
        amount_minor=36000,
        currency="USD",
        state="ACTIVE",
        obligation_id=ids["obligation"],
        dispute_id=ids["dispute"],
    )
    insert(
        "balance_snapshots",
        id=ids["snapshot"],
        currency="USD",
        fetched_at=now,
        as_of_time=now,
        last_refresh_time=now,
        available_minor=150000,
        withheld_minor=0,
        total_minor=150000,
        raw={},
    )
    for index in range(3):
        insert(
            "audit_log",
            actor="shield",
            kind="SHIELD_VERDICT",
            proposal_id=ids["proposal"],
            verdict="ALLOW",
            details={"index": index, "note": "fake-buyer@example.test"},
        )
    return ids
