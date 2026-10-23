"""PostgreSQL constraints run in disposable schemas, never against app tables."""

import re
import threading
import time
from pathlib import Path
from queue import Queue
from uuid import uuid4

import psycopg
import pytest

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]


def _seed_line(conn):
    maker, run, line = uuid4(), uuid4(), uuid4()
    conn.execute("INSERT INTO makers(id, display_name) VALUES (%s, 'Test maker')", (maker,))
    conn.execute(
        "INSERT INTO payout_runs(id, week_start, state) VALUES (%s, '2026-11-02', 'APPROVED')",
        (run,),
    )
    conn.execute(
        "INSERT INTO payout_lines(id,run_id,maker_id,amount_minor,currency,state) "
        "VALUES (%s,%s,%s,100,'USD','APPROVED')", (line, run, maker),
    )
    return maker, run, line


def _attempt(conn, line, number, state):
    operation, attempt = uuid4(), uuid4()
    conn.execute(
        "INSERT INTO operations(id,kind,target_type,target_id,payload,payload_hash,state) "
        "VALUES (%s,'PAYOUT_CREATE','line',%s,'{}',%s,'PENDING')", (operation, line, b"hash"),
    )
    conn.execute(
        "INSERT INTO payout_attempts(id,line_id,attempt_no,operation_id,sender_batch_id,"
        "request_id,sender_item_id,payee_email,amount_minor,currency,state) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,'test@example.invalid',100,'USD',%s)",
        (attempt, line, number, operation, f"batch-{attempt}", f"request-{attempt}",
         f"item-{attempt}", state),
    )
    return attempt


def _journal(conn):
    journal = uuid4()
    conn.execute(
        "INSERT INTO ledger_journals(id,occurred_at,memo) VALUES (%s,now(),'test journal')",
        (journal,),
    )
    return journal


def _post(conn, journal, amount, account="paypal:available", currency="USD"):
    conn.execute(
        "INSERT INTO ledger_postings(journal_id,account,amount_minor,currency) "
        "VALUES (%s,%s,%s,%s)", (journal, account, amount, currency),
    )


def _balanced(conn, journal):
    _post(conn, journal, 100)
    _post(conn, journal, -100, "maker_payable:test")


def test_tables_checks_and_money_match_plan(base_schema):
    plan = (ROOT / "docs/PLAN.md").read_text(encoding="utf-8")
    schema = re.search(r"```sql\n(.*?)-- AFTER THE VERTICAL SLICE", plan, re.DOTALL).group(1)
    tables = set(re.findall(r"CREATE TABLE (\w+)", schema))
    assert len(tables) == 19
    with base_schema.connect() as conn:
        actual = conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema=%s "
            "AND table_type='BASE TABLE' AND table_name <> 'alembic_version'",
            (base_schema.schema,),
        ).fetchall()
        assert {row["table_name"] for row in actual} == tables
        statements = re.split(r"CREATE TABLE ", schema)[1:]
        for statement in statements:
            table = statement.split()[0]
            expected = re.findall(
                r"\b(\w+) text (?:NOT NULL )?CHECK \(\1 IN \((.*?)\)\)", statement, re.DOTALL,
            )
            constraints = conn.execute(
                "SELECT pg_get_constraintdef(c.oid) AS definition FROM pg_constraint c "
                "JOIN pg_class t ON t.oid=c.conrelid "
                "JOIN pg_namespace n ON n.oid=t.relnamespace "
                "WHERE n.nspname=%s AND t.relname=%s AND c.contype='c'",
                (base_schema.schema, table),
            ).fetchall()
            for column, states in expected:
                candidates = [r["definition"] for r in constraints
                              if re.search(rf"\b{column}\b", r["definition"])]
                assert len(candidates) == 1, (table, column, candidates)
                assert re.findall(r"'([^']+)'", candidates[0]) == re.findall(
                    r"'([^']+)'", states,
                ), (table, column)
        columns = conn.execute(
            "SELECT table_name,column_name,data_type,character_maximum_length "
            "FROM information_schema.columns WHERE table_schema=%s",
            (base_schema.schema,),
        ).fetchall()
        money_columns = [row for row in columns if row["column_name"].endswith("_minor")]
        assert len(money_columns) >= 12
        assert all(row["data_type"] == "bigint" for row in money_columns)
        currencies = [row for row in columns if row["column_name"] == "currency"]
        assert currencies
        assert all(row["data_type"] == "character" and row["character_maximum_length"] == 3
                   for row in currencies)
        for table in ("incidents", "proposals"):
            state_checks = conn.execute(
                "SELECT pg_get_constraintdef(c.oid) AS definition FROM pg_constraint c "
                "JOIN pg_class t ON t.oid=c.conrelid "
                "JOIN pg_namespace n ON n.oid=t.relnamespace "
                "WHERE n.nspname=%s AND t.relname=%s AND c.contype='c'",
                (base_schema.schema, table),
            ).fetchall()
            assert not any(re.search(r"\bstate\b", row["definition"]) for row in state_checks)


@pytest.mark.parametrize("state", [
    "PREPARED", "SUBMITTED", "UNKNOWN", "PENDING", "UNCLAIMED", "ONHOLD", "BLOCKED",
])
def test_one_live_attempt_per_line(base_schema, state):
    with base_schema.connect() as conn:
        _, _, line = _seed_line(conn)
        _attempt(conn, line, 1, state)
        with pytest.raises(psycopg.errors.UniqueViolation) as failure, conn.transaction():
            _attempt(conn, line, 2, "PREPARED")
        assert failure.value.diag.constraint_name == "uq_one_live_attempt"
        conn.execute("UPDATE payout_attempts SET state='RETURNED' WHERE line_id=%s", (line,))
        _attempt(conn, line, 2, "PREPARED")
        assert conn.execute(
            "SELECT count(*) AS n FROM payout_attempts WHERE line_id=%s", (line,),
        ).fetchone()["n"] == 2


def test_journal_checks_are_deferred_and_balanced_per_currency(base_schema):
    with base_schema.connect() as conn:
        journal = _journal(conn)
        with conn.transaction():
            _post(conn, journal, 100)
            _post(conn, journal, -100, "maker_payable:test")
            _post(conn, journal, 25, currency="EUR")
            _post(conn, journal, -25, "maker_payable:test", currency="EUR")
        assert conn.execute("SELECT count(*) AS n FROM ledger_postings").fetchone()["n"] == 4
        with pytest.raises(psycopg.errors.CheckViolation) as failure, conn.transaction():
            _post(conn, journal, 1)
            # This statement succeeds; the transaction's exit checks the journal.
            assert conn.execute("SELECT 1 AS n").fetchone()["n"] == 1
        assert failure.value.diag.constraint_name == "ledger_journal_balanced"


def test_cross_currency_amounts_cannot_cancel_each_other(base_schema):
    with base_schema.connect() as conn:
        journal = _journal(conn)
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            _post(conn, journal, 100)
            _post(conn, journal, -100, currency="EUR")


@pytest.mark.parametrize("mutation", ["amount", "currency", "delete", "move"])
def test_journal_mutations_recheck_old_and_new_totals(base_schema, mutation):
    with base_schema.connect() as conn:
        source, target = _journal(conn), _journal(conn)
        with conn.transaction():
            _balanced(conn, source)
            _balanced(conn, target)
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            if mutation == "amount":
                conn.execute("UPDATE ledger_postings SET amount_minor=101 "
                             "WHERE journal_id=%s AND amount_minor=100", (source,))
            elif mutation == "currency":
                conn.execute("UPDATE ledger_postings SET currency='EUR' "
                             "WHERE journal_id=%s AND amount_minor=100", (source,))
            elif mutation == "delete":
                conn.execute("DELETE FROM ledger_postings "
                             "WHERE journal_id=%s AND amount_minor=100", (source,))
            else:
                conn.execute("UPDATE ledger_postings SET journal_id=%s "
                             "WHERE journal_id=%s AND amount_minor=100", (target, source))
        with conn.transaction():
            conn.execute("UPDATE ledger_postings SET journal_id=%s WHERE journal_id=%s",
                         (target, source))
        with conn.transaction():
            conn.execute("DELETE FROM ledger_postings WHERE journal_id=%s", (target,))


def test_postings_require_a_journal(base_schema):
    with base_schema.connect() as conn, pytest.raises(psycopg.errors.NotNullViolation):
        _post(conn, None, 1)


def test_concurrent_journal_writers_serialize(base_schema):
    results = Queue()
    started = Queue()

    def second_writer(journal):
        try:
            with base_schema.connect() as conn:
                conn.execute("SET statement_timeout='8s'")
                started.put(conn.info.backend_pid)
                with conn.transaction():
                    _post(conn, journal, 50)
                    _post(conn, journal, -50, "maker_payable:test")
            results.put(None)
        except psycopg.Error as error:
            results.put(error)

    with base_schema.connect() as conn:
        journal = _journal(conn)
        with conn.transaction():
            _balanced(conn, journal)
            thread = threading.Thread(target=second_writer, args=(journal,), daemon=True)
            thread.start()
            pid = started.get(timeout=5)
            blocked = False
            with base_schema.connect() as observer:
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    row = observer.execute(
                        "SELECT wait_event_type FROM pg_stat_activity WHERE pid=%s", (pid,),
                    ).fetchone()
                    if row and row["wait_event_type"] == "Lock":
                        blocked = True
                        break
                    time.sleep(0.02)
            assert blocked, "second writer did not wait for the first journal transaction"
        thread.join(timeout=10)
        assert not thread.is_alive()
        assert results.get(timeout=1) is None
        assert conn.execute(
            "SELECT sum(amount_minor) AS total FROM ledger_postings WHERE journal_id=%s",
            (journal,),
        ).fetchone()["total"] == 0


def test_downgrade_removes_baseline_objects(base_schema):
    base_schema.migrate("base", downgrade=True)
    with base_schema.connect() as conn:
        tables = conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema=%s",
            (base_schema.schema,),
        ).fetchall()
        assert {row["table_name"] for row in tables} == {"alembic_version"}
        assert conn.execute(
            "SELECT count(*) AS n FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
            "WHERE n.nspname=%s", (base_schema.schema,),
        ).fetchone()["n"] == 0
