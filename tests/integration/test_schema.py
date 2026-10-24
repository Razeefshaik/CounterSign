"""PostgreSQL constraints run in disposable schemas, never against app tables."""

import importlib.util
import json
import re
import threading
import time
from pathlib import Path
from queue import Queue
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.types.json import Jsonb

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


def _dashboard_seed(conn):
    """One connected USD slice; all documents contain real related test rows."""
    ids = {key: uuid4() for key in (
        "maker", "order", "held_order", "obligation", "held_obligation", "run", "old_run",
        "line", "old_line", "operation", "old_operation", "attempt", "old_attempt",
        "dispute", "reservation", "cash", "proposal_cash", "incident", "proposal", "approval",
        "snapshot", "old_proposal",
    )}

    def insert(table, **values):
        query = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
            sql.Identifier(table),
            sql.SQL(",").join(map(sql.Identifier, values)),
            sql.SQL(",").join(sql.Placeholder() for _ in values),
        )
        conn.execute(query, tuple(Jsonb(v) if isinstance(v, (dict, list)) else v
                                  for v in values.values()))

    moment = conn.execute("SELECT now() AS at").fetchone()["at"]
    insert("makers", id=ids["maker"], display_name="Maker test",
           payout_email="maker.test@example.invalid", payout_email_verified_at=moment)
    for name, amount in (("order", 112), ("held_order", 40000)):
        insert("orders", id=ids[name], maker_id=ids["maker"], buyer_ref="buyer-test",
               amount_minor=amount, currency="USD", paypal_order_id=name.upper(),
               tracking={"order_ref": "#" + name})
    for name, order, amount, state in (
        ("obligation", "order", 100, "SCHEDULED"),
        ("held_obligation", "held_order", 36000, "RESERVED"),
    ):
        insert("obligations", id=ids[name], maker_id=ids["maker"], order_id=ids[order],
               kind="ORDER_SHARE", amount_minor=amount, currency="USD",
               due_date="2026-11-06", state=state)
    for name, week in (("run", "2026-11-02"), ("old_run", "2026-10-26")):
        insert("payout_runs", id=ids[name], week_start=week, state="APPROVED")
    for name, run, amount, state in (
        ("line", "run", 100, "PAYING"), ("old_line", "old_run", 25, "UNPAID"),
    ):
        insert("payout_lines", id=ids[name], run_id=ids[run], maker_id=ids["maker"],
               amount_minor=amount, currency="USD", state=state)
    insert("payout_line_obligations", line_id=ids["line"],
           obligation_id=ids["obligation"], amount_minor=100)
    insert("disputes", id=ids["dispute"], paypal_dispute_id="PP-D-TEST",
           order_id=ids["held_order"], status="WAITING_FOR_SELLER_RESPONSE",
           lifecycle_stage="INQUIRY", reason="MERCHANDISE_OR_SERVICE_NOT_RECEIVED",
           amount_minor=40000, currency="USD", seller_response_due_date=moment,
           phase="ACTIVE", links=[{"rel": "provide_evidence"}],
           raw={"hold_observed": True, "staged": True}, last_synced_at=moment)
    subject = {
        "title": "A disputed order", "dispute_id": str(ids["dispute"]),
        "paypal_dispute_id": "PP-D-TEST", "order_id": str(ids["held_order"]),
        "maker_id": str(ids["maker"]), "payout_item_id": None, "run_id": str(ids["run"]),
        "amount_at_risk": {"minor": 40000, "currency": "USD"},
        "deadline": "2026-11-12T23:59:59Z",
    }
    insert("incidents", id=ids["incident"], kind="DISPUTE", subject=subject,
           state="AWAITING_OWNER")
    def money(n):
        return {"minor": n, "currency": "USD"}
    params = {
        "run_id": str(ids["run"]),
        "lines": [{"line_id": str(ids["line"]), "maker_id": str(ids["maker"]),
                   "amount": money(100)}],
        "replacements": [{"line_id": str(ids["old_line"]), "attempt_no": 2,
                          "from_run_id": str(ids["old_run"]),
                          "maker": {"id": str(ids["maker"]), "display_name": "Maker test"},
                          "amount": money(25), "previous_attempt_state": "RETURNED"}],
    }
    insert("proposals", id=ids["proposal"], incident_id=ids["incident"],
           action="SCHEDULE_PAYOUT_RUN", params=params, evidence_refs=["dispute:PP-D-TEST"],
           rationale="Pay permitted shares", expected_effect={"available_change": money(-125)},
           model="test-model", raw_output={"flags": [], "approval_request": {
               "payload_hash": "sha256:" + "ab" * 32, "record_versions": {},
               "expires_at": "2026-11-06T17:00:00Z"}}, verdict="NEEDS_OWNER",
           verdict_details={"rule_results": [
               {"rule": "FUNDING", "outcome": "PASS", "detail": "Covered"}]},
           state="NEEDS_OWNER")
    insert("proposals", id=ids["old_proposal"], incident_id=None,
           action="SCHEDULE_PAYOUT_RUN", params={"run_id": str(ids["old_run"]),
               "lines": [{"line_id": str(ids["old_line"]), "maker_id": str(ids["maker"]),
                          "amount": money(25)}], "replacements": []},
           evidence_refs=[], rationale="Previous weekly run",
           expected_effect={"available_change": money(-25)}, model="test-model",
           raw_output={}, verdict="ALLOW", verdict_details=[], state="EXECUTED")
    conn.execute("UPDATE proposals SET created_at=now()-interval '7 days' WHERE id=%s",
                 (ids["old_proposal"],))
    insert("approvals", id=ids["approval"], proposal_id=ids["proposal"],
           payload_hash=b"\xab" * 32, record_versions={}, approved_by="owner",
           approved_at=moment, expires_at=moment)
    conn.execute("UPDATE payout_runs SET approval_id=%s WHERE id=%s",
                 (ids["approval"], ids["run"]))
    for operation, attempt, line, number, amount, state, age in (
        ("operation", "attempt", "line", 1, 100, "PENDING", "1 hour"),
        ("old_operation", "old_attempt", "old_line", 1, 25, "RETURNED", "3 hours"),
    ):
        insert("operations", id=ids[operation], kind="PAYOUT_CREATE", target_type="line",
               target_id=ids[line], payload={}, payload_hash=b"test-hash",
               proposal_id=ids["proposal"], state="SUCCEEDED", response={
                   "batch_header": {"payout_batch_id": "BATCH-" + attempt}},
               paypal_debug_id="DEBUG-TEST")
        conn.execute("UPDATE operations SET submitted_at=now()-%s::interval WHERE id=%s",
                     (age, ids[operation]))
        insert("payout_attempts", id=ids[attempt], line_id=ids[line], attempt_no=number,
               operation_id=ids[operation], sender_batch_id="batch-" + attempt,
               request_id="request-" + attempt, sender_item_id="item-" + attempt,
               payee_email="maker.test@example.invalid", amount_minor=amount, currency="USD",
               state=state, paypal_batch_id="BATCH-" + attempt, last_checked_at=moment)
    for name, kind, amount in (
        ("reservation", "DISPUTE_EARMARK", 36000), ("cash", "RUN_CASH", 1000),
        ("proposal_cash", "PROPOSAL_CASH", 200),
    ):
        insert("reservations", id=ids[name], kind=kind, amount_minor=amount, currency="USD",
               state="ACTIVE", obligation_id=ids["held_obligation"] if name == "reservation"
               else None, dispute_id=ids["dispute"] if name == "reservation" else None,
               run_id=ids["run"] if name == "cash" else None,
               proposal_id=ids["proposal"] if name == "proposal_cash" else None)
    insert("balance_snapshots", id=ids["snapshot"], fetched_at=moment, as_of_time=moment,
           last_refresh_time=moment, available_minor=134000, withheld_minor=46000,
           total_minor=180000, currency="USD", raw={})
    conn.execute("UPDATE balance_snapshots SET last_refresh_time=now()-interval '2 hours',"
                 "as_of_time=now()-interval '2 hours'")
    insert("audit_log", actor="shield", kind="SHIELD_VERDICT", proposal_id=ids["proposal"],
           operation_id=ids["operation"], verdict="ALLOW", details={"rules_evaluated": 1})
    return ids


def _schema_validate(document, name):
    checker_spec = importlib.util.spec_from_file_location(
        "countersign_dashboard_schema_check", ROOT / "tests/dashboard/schema_check.py",
    )
    checker = importlib.util.module_from_spec(checker_spec)
    checker_spec.loader.exec_module(checker)
    schema = json.loads((ROOT / "tests/dashboard/schemas" / (name + ".json")).read_text())
    checker.validate(document, schema)


def test_dashboard_documents_match_mate_schemas(db_schema):
    with db_schema.connect() as conn:
        ids = _dashboard_seed(conn)
        for view, schema in (
            ("v_dash_incidents", "incidents"), ("v_dash_proposals", "proposals"),
            ("v_dash_runs", "payout-runs"), ("v_dash_makers", "makers"),
            ("v_dash_disputes", "disputes"), ("v_dash_reservations", "reservations"),
            ("v_dash_audit", "audit-log"),
        ):
            # Mate's fixture-derived schemas overfit some optional associations
            # and pending-request states. Validate each documented example here;
            # legitimate nullable cash links/executed runs are asserted below.
            predicate = sql.SQL("")
            values = ()
            if view == "v_dash_reservations":
                predicate = sql.SQL(" WHERE kind='DISPUTE_EARMARK'")
            elif view == "v_dash_proposals":
                predicate, values = sql.SQL(" WHERE id=%s"), (ids["proposal"],)
            rows = conn.execute(sql.SQL("SELECT doc FROM {}{}").format(
                sql.Identifier(view), predicate), values).fetchall()
            assert rows, view
            _schema_validate({"items": [r["doc"] for r in rows], "next_cursor": None}, schema)
        for view, schema in (("v_dash_incident_detail", "incidents-detail"),
                             ("v_dash_run_detail", "payout-runs-detail")):
            document = conn.execute(sql.SQL("SELECT doc FROM {} WHERE id=%s").format(
                sql.Identifier(view)), (ids["incident" if "incident" in view else "run"],)
            ).fetchone()["doc"]
            _schema_validate(document, schema)
        _schema_validate(conn.execute("SELECT doc FROM v_dash_cash").fetchone()["doc"], "cash")
        graph = conn.execute("SELECT dash_money_graph(%s,NULL,NULL) AS doc",
                             (ids["dispute"],)).fetchone()["doc"]
        _schema_validate(graph, "money-graph")
        assert graph["nodes"] and graph["edges"]
        assert {n["type"] for n in graph["nodes"]} == {
            "dispute", "order", "obligation", "maker", "reservation", "line", "attempt",
        }
        for view, columns in {
            "v_dash_incidents": {"id", "state", "kind", "updated_at"},
            "v_dash_proposals": {"id", "incident_id", "state", "verdict", "action", "created_at"},
            "v_dash_runs": {"id", "state", "week_start"},
            "v_dash_makers": {"id", "display_name"},
            "v_dash_disputes": {"id", "phase", "status", "updated_at"},
            "v_dash_reservations": {"id", "state", "kind", "dispute_id", "run_id", "created_at"},
            "v_dash_audit": {"id", "at", "proposal_id", "operation_id", "actor", "kind"},
        }.items():
            actual = conn.execute("SELECT column_name FROM information_schema.columns "
                                  "WHERE table_schema=%s AND table_name=%s",
                                  (db_schema.schema, view)).fetchall()
            assert columns | {"doc"} <= {r["column_name"] for r in actual}


def test_dashboard_cash_and_replacement_totals(db_schema):
    with db_schema.connect() as conn:
        ids = _dashboard_seed(conn)
        cash = conn.execute("SELECT doc FROM v_dash_cash").fetchone()["doc"]
        assert cash["outflows_after_refresh"]["total"]["minor"] == 100
        assert cash["cash_reservations"]["total"]["minor"] == 1200
        assert cash["effective_available"]["minor"] == 133900
        assert cash["unreserved"]["minor"] == 132700
        assert cash["earmarks_not_subtracted"]["dispute"]["minor"] == 36000
        assert cash["paypal"]["age_seconds"] >= 7200
        assert cash["paypal"]["stale"] is False
        assert len(cash["outflows_after_refresh"]["items"]) == 1
        run = conn.execute("SELECT doc FROM v_dash_run_detail WHERE id=%s",
                           (ids["run"],)).fetchone()["doc"]
        assert run["total"]["minor"] == 125
        assert run["reserved_not_paid"]["minor"] == 36000
        assert run["line_count"] == run["replacement_count"] == 1
        assert run["replacements"][0]["previous_attempt_state"] == "RETURNED"
        for row in conn.execute("SELECT doc FROM v_dash_reservations WHERE kind<>'DISPUTE_EARMARK'"):
            assert row["doc"]["obligation_id"] is None
            assert row["doc"]["dispute_id"] is None
        previous = conn.execute("SELECT doc FROM v_dash_proposals WHERE id=%s",
                                (ids["old_proposal"],)).fetchone()["doc"]
        assert previous["state"] == "EXECUTED" and "approval_request" not in previous
        conn.execute("INSERT INTO policies(key,value) VALUES ('cash_stale_seconds','1')")
        assert conn.execute("SELECT doc FROM v_dash_cash").fetchone()["doc"]["paypal"]["stale"]


def test_dashboard_unknown_cash_never_invents_a_balance(db_schema):
    with db_schema.connect() as conn:
        assert conn.execute("SELECT doc FROM v_dash_cash").fetchall() == []
        ids = _dashboard_seed(conn)
        conn.execute("UPDATE balance_snapshots SET available_minor=NULL")
        assert conn.execute("SELECT doc FROM v_dash_cash").fetchall() == []
        conn.execute("UPDATE balance_snapshots SET available_minor=134000")
        conn.execute("UPDATE balance_snapshots SET as_of_time=NULL")
        assert conn.execute("SELECT doc FROM v_dash_cash").fetchall() == []
        conn.execute("UPDATE balance_snapshots SET as_of_time=last_refresh_time")
        conn.execute("DELETE FROM payout_attempts WHERE id=%s", (ids["attempt"],))
        assert conn.execute("SELECT doc FROM v_dash_cash").fetchall() == []


def test_dashboard_role_reads_only_views_and_fixed_graph(db_schema):
    with db_schema.connect() as conn:
        ids = _dashboard_seed(conn)
        role = conn.execute("SELECT rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,rolbypassrls "
                            "FROM pg_roles WHERE rolname='countersign_agent_ro'").fetchone()
        assert role and not any(role.values())
        conn.execute("SET ROLE countersign_agent_ro")
        try:
            assert conn.execute("SELECT doc FROM v_dash_makers").fetchall()
            assert conn.execute("SELECT dash_money_graph(NULL,%s,NULL) AS doc",
                                (ids["maker"],)).fetchone()["doc"]["nodes"]
            for statement in ("SELECT * FROM makers", "SELECT * FROM operations",
                              "SELECT * FROM webhook_inbox", ("INSERT INTO makers(id,display_name) "
                              "VALUES ('00000000-0000-0000-0000-000000000001','Denied')"),
                              "UPDATE v_dash_reservations SET kind='RUN_CASH'",
                              "DELETE FROM v_dash_reservations"):
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    conn.execute(statement)
        finally:
            conn.execute("RESET ROLE")
        grants = conn.execute("SELECT table_name,privilege_type FROM information_schema.role_table_grants "
                              "WHERE grantee='countersign_agent_ro' AND table_schema=%s",
                              (db_schema.schema,)).fetchall()
        assert grants and all(g["table_name"].startswith("v_dash_")
                              and g["privilege_type"] == "SELECT" for g in grants)


def test_dashboard_graph_roots_and_search_path(db_schema):
    with db_schema.connect() as conn:
        ids = _dashboard_seed(conn)
        for params in ((None, None, None), (ids["dispute"], ids["maker"], None)):
            with pytest.raises(psycopg.errors.InvalidParameterValue):
                conn.execute("SELECT dash_money_graph(%s,%s,%s)", params)
        assert conn.execute("SELECT dash_money_graph(%s,NULL,NULL) AS doc",
                            (uuid4(),)).fetchone()["doc"] == {"nodes": [], "edges": []}
        run_graph = conn.execute("SELECT dash_money_graph(NULL,NULL,%s) AS doc",
                                 (ids["run"],)).fetchone()["doc"]
        assert "line:" + str(ids["old_line"]) in {n["id"] for n in run_graph["nodes"]}
        conn.execute("CREATE TEMP TABLE makers(id uuid,display_name text)")
        conn.execute("SET search_path=pg_temp,public")
        conn.execute("SET ROLE countersign_agent_ro")
        try:
            graph = conn.execute(sql.SQL("SELECT {}.dash_money_graph(NULL,%s,NULL) AS doc").format(
                sql.Identifier(db_schema.schema)), (ids["maker"],)).fetchone()["doc"]
            assert "maker:" + str(ids["maker"]) in {n["id"] for n in graph["nodes"]}
            assert conn.execute(sql.SQL("SELECT doc FROM {}.v_dash_makers").format(
                sql.Identifier(db_schema.schema))).fetchone()["doc"]["display_name"] == "Maker test"
        finally:
            conn.execute("RESET ROLE")


def test_dashboard_masks_nested_documents_and_keeps_utc(db_schema):
    with db_schema.connect() as conn:
        ids = _dashboard_seed(conn)
        conn.execute("UPDATE proposals SET rationale=%s,raw_output=jsonb_set(raw_output,'{flags}',%s) "
                     "WHERE id=%s", ("Contact owner.person@example.invalid", Jsonb([{
                         "type": "PROMPT_INJECTION", "excerpt": "attacker@example.invalid",
                         "nested": {"access_token": "fake-private-token"}}]), ids["proposal"]))
        conn.execute("SET TIME ZONE 'Asia/Kolkata'")
        proposal = conn.execute("SELECT doc FROM v_dash_proposals WHERE id=%s",
                                (ids["proposal"],)).fetchone()["doc"]
        maker = conn.execute("SELECT doc FROM v_dash_makers").fetchone()["doc"]
        rendered = json.dumps([proposal, maker])
        for private in ("owner.person@example.invalid", "attacker@example.invalid",
                        "maker.test@example.invalid", "fake-private-token", "access_token"):
            assert private not in rendered
        assert "o***@example.invalid" in rendered
        assert maker["payout_email_masked"] == "m***@example.invalid"
        assert proposal["created_at"].endswith("Z")
        conn.execute("UPDATE disputes SET raw=raw-'hold_observed'")
        assert conn.execute("SELECT doc FROM v_dash_disputes").fetchone()["doc"]["hold_observed"] is None


def test_dashboard_usd_summaries_do_not_mix_currencies(db_schema):
    with db_schema.connect() as conn:
        ids = _dashboard_seed(conn)
        conn.execute("INSERT INTO obligations(id,maker_id,kind,amount_minor,currency,due_date,state) "
                     "VALUES (%s,%s,'ADJUSTMENT',999999,'EUR','2026-11-06','OPEN')",
                     (uuid4(), ids["maker"]))
        maker = conn.execute("SELECT doc FROM v_dash_makers").fetchone()["doc"]
        assert maker["owed_open"] == {"minor": 0, "currency": "USD"}
        conn.execute("UPDATE payout_lines SET currency='EUR' WHERE id=%s", (ids["line"],))
        assert conn.execute("SELECT doc FROM v_dash_runs WHERE id=%s", (ids["run"],)).fetchall() == []
        cash = conn.execute("SELECT doc FROM v_dash_cash").fetchone()["doc"]
        assert cash["currency"] == "USD" and cash["unreserved"]["minor"] == 132700


def test_dashboard_downgrade_keeps_other_schema_role(db_schema):
    db_schema.migrate("0001", downgrade=True)
    with db_schema.connect() as conn:
        assert not conn.execute("SELECT table_name FROM information_schema.views "
                                "WHERE table_schema=%s", (db_schema.schema,)).fetchall()
        assert conn.execute("SELECT rolcanlogin FROM pg_roles WHERE rolname='countersign_agent_ro'"
                            ).fetchone()["rolcanlogin"] is False
        assert conn.execute("SELECT count(*) AS n FROM makers").fetchone()["n"] == 0
