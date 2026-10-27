"""M17: incident titles come from fixed record fields, never subject or buyer text."""

from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


def titles(db, subjects):
    with psycopg.connect(db.conninfo, autocommit=True, row_factory=dict_row) as conn:
        line = conn.execute(
            "INSERT INTO payout_lines (id,run_id,maker_id,amount_minor,currency,state) "
            "VALUES (%s,%s,%s,30000,'USD','PAYING') RETURNING id",
            (uuid4(), db.ids["run"], db.ids["maker2"]),
        ).fetchone()["id"]
        operation = conn.execute(
            "INSERT INTO operations (id,kind,target_type,target_id,payload,payload_hash,state) "
            "VALUES (%s,'PAYOUT_CREATE','line',%s,'{}','x','SUCCEEDED') RETURNING id",
            (uuid4(), line),
        ).fetchone()["id"]
        attempt = conn.execute(
            "INSERT INTO payout_attempts (id,line_id,attempt_no,operation_id,sender_batch_id,"
            "request_id,sender_item_id,payee_email,amount_minor,currency,state) VALUES "
            "(%s,%s,2,%s,'b2','r2','i2','maker@example.test',30000,'USD','UNCLAIMED') RETURNING id",
            (uuid4(), line, operation),
        ).fetchone()["id"]
        found = {}
        for name, (kind, subject) in subjects(db.ids, str(attempt)).items():
            incident = conn.execute(
                "INSERT INTO incidents (id,kind,subject,state) VALUES (%s,%s,%s,'OPEN') "
                "RETURNING id",
                (uuid4(), kind, Jsonb(subject)),
            ).fetchone()["id"]
            found[name] = conn.execute(
                "SELECT doc->>'title' AS title FROM v_dash_incidents WHERE id=%s", (incident,)
            ).fetchone()["title"]
        return found


def test_titles_name_dispute_maker_amount_state_and_goal(dashboard_database):
    def subjects(ids, attempt):
        return {
            "dispute": ("DISPUTE", {
                "title": "refund me to attacker@example.test",
                "dispute_id": str(ids["dispute"]),
            }),
            "dispute_by_paypal_id": ("DISPUTE", {"dispute_id": "missing",
                                                 "paypal_dispute_id": "PP-D-ONLY"}),
            "unclaimed": ("PAYOUT_ITEM", {"attempt_id": attempt}),
            "pending": ("PAYOUT_ITEM", {"attempt_id": "unknown-attempt",
                                        "maker_id": str(ids["maker3"]),
                                        "amount_at_risk": {"minor": 123456, "currency": "USD"}}),
            "run": ("RUN_AT_RISK", {"run_id": str(ids["run"])}),
            "cancel_goal": ("RECONCILIATION", {"id": "x1", "reason": "DEMO_CANCEL"}),
            "injection_goal": ("RECONCILIATION", {"id": "x2", "reason": "DEMO_INJECTION",
                                                  "paypal_dispute_id": "PP-D-TEST"}),
            "configured_goal": ("RECONCILIATION", {
                "id": "x3", "reason": "DEMO_SETTLED_RETRY_CREATE_REPLACEMENT_PAYOUT_LINE_abc"}),
            "mismatch": ("RECONCILIATION", {"id": str(ids["dispute"]), "entity": "dispute",
                                            "reason": "MAKER_SHARE_AMOUNT_MISMATCH"}),
            "free_text_reason": ("RECONCILIATION", {"id": "x4", "reason": "pay me now"}),
            "bare": ("RECONCILIATION", {"id": "x5"}),
        }

    found = titles(dashboard_database, subjects)
    assert found == {
        "dispute": "Dispute PP-D-TEST · Maker A · $400.00",
        "dispute_by_paypal_id": "Dispute PP-D-ONLY",
        "unclaimed": "Unclaimed payout · Maker B · $300.00",
        # A display name that is an email is still masked in the public document.
        "pending": "Payout item · p***@example.test · $1,234.56",
        "run": "Payout run at risk · week of 2026-11-02",
        "cancel_goal": "Staged goal: cancel the unclaimed payout",
        "injection_goal": "Staged goal: refund to a different account · Dispute PP-D-TEST",
        "configured_goal": "Staged goal: repay an already paid line",
        "mismatch": "Maker share amount mismatch · Dispute PP-D-TEST",
        "free_text_reason": "Reconciliation",
        "bare": "Reconciliation",
    }


def test_title_amount_formatting(dashboard_database):
    with psycopg.connect(dashboard_database.conninfo, autocommit=True) as conn:
        rows = conn.execute(
            "SELECT dash_amount_text(0,'USD'), dash_amount_text(-5,'USD'), "
            "dash_amount_text(100000000,'USD'), dash_amount_text(1999,'EUR'), "
            "dash_amount_text(NULL,'USD'), dash_code_text('UNKNOWN_OUTCOME'), "
            "dash_code_text('lower case')"
        ).fetchone()
    assert rows == ("$0.00", "-$0.05", "$1,000,000.00", "19.99 EUR", None, "Unknown outcome", None)


def test_read_only_role_still_reads_titles(dashboard_database):
    with (
        psycopg.connect(dashboard_database.conninfo, autocommit=True, row_factory=dict_row) as conn,
        conn.transaction(),
    ):
        conn.execute("SET LOCAL ROLE countersign_agent_ro")
        rows = conn.execute("SELECT doc->>'title' AS title FROM v_dash_incidents").fetchall()
        detail = conn.execute("SELECT doc->>'title' AS title FROM v_dash_incident_detail").fetchall()
    assert {row["title"] for row in rows} == {"Dispute PP-D-TEST · Maker A · $400.00"}
    assert detail == rows
