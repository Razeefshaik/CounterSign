"""Small constraint-valid records for testing raw SQL independently of repositories."""

from uuid import uuid4

from psycopg import sql
from psycopg.types.json import Jsonb

TABLES = {
    "obligation": "obligations",
    "line": "payout_lines",
    "attempt": "payout_attempts",
    "operation": "operations",
    "proposal": "proposals",
    "incident": "incidents",
}


def seed(conn, initial=None):
    states = {
        "obligation": "OPEN",
        "line": "APPROVED",
        "attempt": "PREPARED",
        "operation": "PENDING",
        "proposal": "CREATED",
        "incident": "OPEN",
    }
    states.update(initial or {})
    ids = {name: uuid4() for name in (*TABLES, "maker", "run")}
    conn.execute(
        "INSERT INTO makers(id,display_name) VALUES (%s,'Property maker')", (ids["maker"],)
    )
    conn.execute(
        "INSERT INTO payout_runs(id,week_start,state) VALUES (%s,'2026-11-02','APPROVED')",
        (ids["run"],),
    )
    conn.execute(
        "INSERT INTO payout_lines(id,run_id,maker_id,amount_minor,currency,state) "
        "VALUES (%s,%s,%s,100,'USD',%s)",
        (ids["line"], ids["run"], ids["maker"], states["line"]),
    )
    conn.execute(
        "INSERT INTO obligations(id,maker_id,kind,amount_minor,currency,due_date,state) "
        "VALUES (%s,%s,'ADJUSTMENT',100,'USD','2026-11-06',%s)",
        (ids["obligation"], ids["maker"], states["obligation"]),
    )
    conn.execute(
        "INSERT INTO operations(id,kind,target_type,target_id,payload,payload_hash,state) "
        "VALUES (%s,'PAYOUT_CREATE','payout_line',%s,'{}',%s,%s)",
        (ids["operation"], ids["line"], b"fake-hash", states["operation"]),
    )
    attempt(conn, ids["line"], ids["operation"], 1, states["attempt"], row_id=ids["attempt"])
    conn.execute(
        "INSERT INTO proposals(id,action,params,evidence_refs,model,raw_output,state) "
        "VALUES (%s,'WAIT','{}','[]','property','{}',%s)",
        (ids["proposal"], states["proposal"]),
    )
    conn.execute(
        "INSERT INTO incidents(id,kind,subject,state) VALUES (%s,'RECONCILIATION',%s,%s)",
        (ids["incident"], Jsonb({"id": "property"}), states["incident"]),
    )
    return ids


def attempt(conn, line_id, operation_id, number, state, *, row_id=None):
    row_id = row_id or uuid4()
    conn.execute(
        "INSERT INTO payout_attempts(id,line_id,attempt_no,operation_id,sender_batch_id,request_id,"
        "sender_item_id,payee_email,amount_minor,currency,state) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,'property@example.invalid',100,'USD',%s)",
        (row_id, line_id, number, operation_id, str(row_id), str(row_id), str(row_id), state),
    )
    return row_id


def update(conn, entity, row_id, state):
    return conn.execute(
        sql.SQL("UPDATE {} SET state=%s WHERE id=%s").format(sql.Identifier(TABLES[entity])),
        (str(state), row_id),
    )


def state(conn, entity, row_id):
    return conn.execute(
        sql.SQL("SELECT state FROM {} WHERE id=%s").format(sql.Identifier(TABLES[entity])),
        (row_id,),
    ).fetchone()["state"]
