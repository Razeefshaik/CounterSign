"""C62: trusted image checkpoint data; all database restoration is transactional.

The fixed table list and JSON record conversion retain native PostgreSQL types,
constraints and the existing pool. No request can choose tables or supply data.
"""

import hashlib
import json
import shutil
from pathlib import Path

from psycopg import sql
from psycopg.pq import TransactionStatus
from psycopg.types.json import Jsonb

from countersign.db.repositories import TABLES


class FridayError(RuntimeError):
    """Fixed value-free diagnostics at the demo boundary."""


def identity(settings, emails, app_url, *, settlement="adjudicate",
             reason_a="MERCHANDISE_OR_SERVICE_NOT_RECEIVED", evidence_branch=True, model=None):
    return {
        "database":settings.database_url, "base":settings.paypal.base_url,
        "client":settings.paypal.client_id, "webhook":settings.paypal.webhook_id,
        "makers":emails, "settlement":settlement, "reason_a":reason_a,
        "evidence_branch":evidence_branch, "app":app_url,
        **({"model":model} if model is not None else {}),
    }


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


async def capture_database(conn):
    tables = {}
    for name in TABLES:
        row = await (await conn.execute(sql.SQL(
            "SELECT coalesce(jsonb_agg(to_jsonb(t)),'[]'::jsonb) AS rows FROM {} t"
        ).format(sql.Identifier(name)))).fetchone()
        tables[name] = row["rows"]
    return {"format":1, "tables":tables}


def validate_database(document):
    if document.get("format") != 1 or set(document.get("tables", {})) != set(TABLES):
        raise FridayError("FRIDAY_DATA_INVALID")
    for name, rows in document["tables"].items():
        if not isinstance(rows, list) or any(not isinstance(row, dict) or set(row) - TABLES[name].columns for row in rows):
            raise FridayError("FRIDAY_DATA_INVALID")
    proposals = document["tables"]["proposals"]
    waiting = [p for p in proposals if p["action"] == "SCHEDULE_PAYOUT_RUN" and p["state"] == "NEEDS_OWNER"]
    if len(waiting) != 1 or document["tables"]["approvals"]:
        raise FridayError("FRIDAY_NOT_UNSIGNED")
    return waiting[0]


async def restore_database(conn, document):
    validate_database(document)
    if conn.info.transaction_status != TransactionStatus.INTRANS:
        raise FridayError("FRIDAY_TRANSACTION_REQUIRED")
    # Same financial lock as owner decisions. Keep every FK/CHECK/ledger trigger.
    await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                       ("countersign:owner-decisions",))
    tables = sql.SQL(", ").join(sql.Identifier(name) for name in [*TABLES, "passkey_challenges"])
    await conn.execute(sql.SQL("TRUNCATE TABLE {} RESTART IDENTITY").format(tables))
    for name, spec in TABLES.items():
        rows = document["tables"][name]
        if not rows:
            continue
        # audit IDs are serial defaults, not generated columns; preserve history IDs.
        columns = sorted(spec.columns - (spec.generated if name != "audit_log" else set()))
        names = sql.SQL(", ").join(sql.Identifier(column) for column in columns)
        await conn.execute(sql.SQL(
            "INSERT INTO {} ({}) SELECT {} FROM jsonb_populate_recordset(NULL::{}, %s)"
        ).format(sql.Identifier(name), names, names, sql.Identifier(name)), (Jsonb(rows),))
    await conn.execute(
        "SELECT setval(pg_get_serial_sequence('audit_log','id'),"
        "coalesce((SELECT max(id) FROM audit_log),1),EXISTS(SELECT 1 FROM audit_log))"
    )
    # owner_passkeys/sign counters survive; old challenges cannot approve this restore.


class Bundle:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()

    def file(self, relative):
        path = self.directory / relative
        if path.is_symlink() or not path.resolve().is_relative_to(self.directory):
            raise FridayError("FRIDAY_FILE_REFUSED")
        return path

    def load(self):
        manifest = json.loads(self.file("manifest.json").read_text(encoding="utf-8"))
        if manifest.get("format") != 1 or manifest.get("checkpoint") != "friday":
            raise FridayError("FRIDAY_MANIFEST_INVALID")
        required = {"database.json", "mock.json", "week/" + manifest["date"] + "/state.json"}
        if not required <= manifest.get("files", {}).keys():
            raise FridayError("FRIDAY_MANIFEST_INVALID")
        for name, expected in manifest["files"].items():
            if hashlib.sha256(self.file(name).read_bytes()).hexdigest() != expected:
                raise FridayError("FRIDAY_CHECKSUM_FAILED")
        database = json.loads(self.file("database.json").read_text(encoding="utf-8"))
        waiting = validate_database(database)
        if waiting["id"] != manifest["proposal_id"]:
            raise FridayError("FRIDAY_PROPOSAL_MISMATCH")
        state = json.loads(self.file("week/" + manifest["date"] + "/state.json").read_text(encoding="utf-8"))
        expected = {"setup", "seed", "monday_dispute", "wednesday_dispute", "cancel_and_verify", "friday_plan"}
        if state.get("target") != "mock" or {k for k, v in state.get("steps", {}).items() if v == "SUCCEEDED"} != expected:
            raise FridayError("FRIDAY_WEEK_INVALID")
        return manifest, database

    def week(self, destination, settings, app_url):
        manifest, _ = self.load()
        destination = Path(destination)
        if destination.exists():
            raise FridayError("FRIDAY_DESTINATION_EXISTS")
        source = self.file("week")
        if any(path.is_symlink() for path in source.rglob("*")):
            raise FridayError("FRIDAY_FILE_REFUSED")
        shutil.copytree(source, destination)
        path = destination / manifest["date"] / "state.json"
        state = json.loads(path.read_text(encoding="utf-8"))
        if state.get("target") != "mock":
            raise FridayError("FRIDAY_TARGET_REFUSED")
        emails = {key:"maker-" + key.lower() + "@example.test" for key in "ABC"}
        state["identity_hash"] = fingerprint(identity(settings, emails, app_url))
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
        (destination / ".friday").write_text("friday\n", encoding="ascii")
        return destination
