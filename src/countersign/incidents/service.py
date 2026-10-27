"""Discover incidents, synchronize execution, and close only after reconciler agreement."""

import hashlib
import json
from datetime import UTC, datetime

from psycopg.types.json import Jsonb

from countersign.db import Repositories, cas_transition
from countersign.fsm.incident import INCIDENT
from countersign.fsm.incident import IncidentEvent as E
from countersign.ledger.journal import serialize_on
from countersign.simulator.service import rows, simulate_run

from .checks import dispute_agrees, identifier, payout_agrees, run_agrees

KINDS = frozenset({"DISPUTE", "PAYOUT_ITEM", "RUN_AT_RISK", "RECONCILIATION"})


class IncidentService:
    def __init__(self, database, *, clock=None):
        self.database = database
        self.clock = clock or (lambda: datetime.now(UTC))

    async def _audit(self, conn, kind, incident, **details):
        await Repositories(conn).audit_log.insert(
            actor="system", kind=kind, details={"incident_id": str(incident["id"]), **details}
        )

    async def _opened(self, conn, incident):
        recorded = await rows(
            conn,
            "SELECT 1 FROM audit_log WHERE kind='INCIDENT_OPENED' "
            "AND details->>'incident_id'=%s LIMIT 1",
            (str(incident["id"]),),
        )
        if not recorded:
            await self._audit(conn, "INCIDENT_OPENED", incident, kind_name=incident["kind"])

    async def _open(self, conn, kind, subject, *, identity):
        # Match historical inbox/executor subjects by identity, not their optional fields.
        match = {identity: str(subject[identity])}
        if kind == "RECONCILIATION" and "entity" in subject:
            match["entity"] = subject["entity"]
        previous = await rows(
            conn,
            "SELECT * FROM incidents WHERE kind=%s "
            "AND subject @> %s ORDER BY (state='RESOLVED'),opened_at DESC,id DESC LIMIT 1",
            (kind, Jsonb(match)),
        )
        if previous:
            old = previous[0]
            if old["state"] != "RESOLVED":
                return old
            current = await self._observation(conn, old, "RECONCILED")
            recorded = await rows(
                conn,
                "SELECT details->>'observation' AS observation FROM audit_log "
                "WHERE kind='STATE_TRANSITION' AND details->>'event'='INCIDENT_OBSERVED' "
                "AND details->>'incident_id'=%s ORDER BY id DESC LIMIT 1",
                (str(old["id"]),),
            )
            if recorded and recorded[0]["observation"] == current:
                return old
        incident = await Repositories(conn).incidents.insert(
            kind=kind, subject=subject, state="OPEN", opened_at=self.clock()
        )
        await self._opened(conn, incident)
        return incident

    async def open(self, kind, subject):
        """Trusted application hook for all schema kinds; no model-provided prose."""
        identities = {
            "DISPUTE": "dispute_id",
            "PAYOUT_ITEM": "attempt_id",
            "RUN_AT_RISK": "run_id",
            "RECONCILIATION": "id",
        }
        allowed = {
            "dispute_id",
            "paypal_dispute_id",
            "order_id",
            "maker_id",
            "attempt_id",
            "payout_item_id",
            "run_id",
            "operation_id",
            "entity",
            "id",
            "reason",
        }
        key = identities.get(kind)
        if (
            kind not in KINDS
            or not isinstance(subject, dict)
            or set(subject) - allowed
            or key not in subject
            or any(not isinstance(v, str) for v in subject.values())
        ):
            raise ValueError("INVALID_INCIDENT_SUBJECT")
        # IDs and fixed codes only: never put an email, buyer text or a URL in an incident.
        import re

        if any(not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", v) for v in subject.values()):
            raise ValueError("INVALID_INCIDENT_SUBJECT")
        async with self.database.tx() as conn:
            await serialize_on(conn, "countersign:owner-decisions")
            return await self._open(conn, kind, subject, identity=key)

    async def transition(self, incident_id, event, *, expected_state, expected_version):
        """Use the existing FSM/CAS guard. RECONCILED belongs exclusively to sweep()."""
        if E(event) is E.RECONCILED:
            raise ValueError("RECONCILIATION_REQUIRED")
        async with self.database.tx() as conn:
            await serialize_on(conn, "countersign:owner-decisions")
            incident = await Repositories(conn).incidents.get(
                identifier(incident_id), for_update=True
            )
            state = await cas_transition(
                conn, INCIDENT, "incidents", incident["id"], expected_state, expected_version, event
            )
            await self._audit(
                conn, "STATE_TRANSITION", incident, event=str(event), state=str(state)
            )
            return str(state)

    async def _move(self, conn, incident, event):
        state = await cas_transition(
            conn,
            INCIDENT,
            "incidents",
            incident["id"],
            incident["state"],
            incident["version"],
            event,
        )
        await self._audit(conn, "STATE_TRANSITION", incident, event=str(event), state=str(state))
        return {**incident, "state": str(state), "version": incident["version"] + 1}

    async def _discover(self, conn):
        repos = Repositories(conn)
        for dispute in await rows(conn, "SELECT * FROM disputes ORDER BY id"):
            subject = {
                "dispute_id": str(dispute["id"]),
                "paypal_dispute_id": dispute["paypal_dispute_id"],
            }
            order = await repos.orders.get(dispute["order_id"]) if dispute["order_id"] else None
            if order:
                subject.update(order_id=str(order["id"]), maker_id=str(order["maker_id"]))
            await self._open(conn, "DISPUTE", subject, identity="dispute_id")
        for attempt in await rows(
            conn,
            "SELECT * FROM payout_attempts WHERE state IN "
            "('UNKNOWN','UNCLAIMED','ONHOLD','BLOCKED','FAILED','RETURNED','REFUNDED','REVERSED') ORDER BY id",
        ):
            subject = {
                "attempt_id": str(attempt["id"]),
                "operation_id": str(attempt["operation_id"]),
                "amount_at_risk": {
                    "minor": attempt["amount_minor"],
                    "currency": attempt["currency"].strip(),
                },
            }
            if attempt["paypal_item_id"]:
                subject["payout_item_id"] = attempt["paypal_item_id"]
            line = await repos.payout_lines.get(attempt["line_id"])
            if line:
                subject.update(run_id=str(line["run_id"]), maker_id=str(line["maker_id"]))
            await self._open(conn, "PAYOUT_ITEM", subject, identity="attempt_id")
        for run in await rows(
            conn,
            "SELECT * FROM payout_runs WHERE state IN ('DRAFT','AWAITING_OWNER','APPROVED') ORDER BY id",
        ):
            result = await simulate_run(conn, run["id"])
            if (
                result.get("reason") == "CASH_UNKNOWN"
                or result.get("shortfall", {}).get("minor", 0) > 0
            ):
                await self._open(conn, "RUN_AT_RISK", {"run_id": str(run["id"])}, identity="run_id")

    async def _agreement(self, conn, incident, *, since):
        subject = incident["subject"]
        kind = incident["kind"]
        if kind == "RECONCILIATION":
            entity = subject.get("entity")
            if entity == "dispute":
                kind, subject = "DISPUTE", {"dispute_id": subject.get("id")}
            elif entity == "attempt":
                kind, subject = "PAYOUT_ITEM", {"attempt_id": subject.get("id")}
            else:
                return False, "RECONCILIATION_SUBJECT_REQUIRES_INVESTIGATION"
        if kind == "DISPUTE":
            return await dispute_agrees(conn, subject.get("dispute_id"), since=since)
        if kind == "PAYOUT_ITEM":
            return await payout_agrees(conn, subject.get("attempt_id"), since=since)
        if kind == "RUN_AT_RISK":
            return await run_agrees(conn, subject.get("run_id"), since=since)
        return False, "INCIDENT_SUBJECT_UNAVAILABLE"

    async def _execution(self, conn, incident):
        proposals = await rows(
            conn,
            "SELECT * FROM proposals WHERE incident_id=%s ORDER BY created_at DESC,id DESC LIMIT 1",
            (incident["id"],),
        )
        if not proposals:
            return incident
        proposal = proposals[0]
        if proposal["state"] == "EXECUTING" and incident["state"] in {
            "INVESTIGATING",
            "AWAITING_OWNER",
        }:
            incident = await self._move(conn, incident, E.EXECUTION_STARTED)
        if (
            proposal["state"] == "EXECUTED"
            and incident["state"] in {"INVESTIGATING", "AWAITING_OWNER"}
            and proposal["action"] not in {"WAIT", "DRAFT_MESSAGE", "REQUEST_OWNER"}
        ):
            incident = await self._move(conn, incident, E.EXECUTION_STARTED)
        if incident["state"] == "EXECUTING":
            event = {
                "EXECUTED": E.EXECUTION_SUCCEEDED,
                "FAILED": E.EXECUTION_FAILED,
                "BLOCKED_AT_EXECUTION": E.RECHECK_FAILED,
            }.get(proposal["state"])
            if event:
                incident = await self._move(conn, incident, event)
        return incident

    async def sweep(self, *, since, reconciled=True):
        """Caller supplies the poll start; pending writes and unconfirmed states cannot close a case."""
        if not isinstance(since, datetime) or since.utcoffset() is None:
            raise ValueError("RECONCILIATION_TIME_REQUIRED")
        async with self.database.tx() as conn:
            await serialize_on(conn, "countersign:owner-decisions")
            await self._discover(conn)
            incidents = await rows(
                conn, "SELECT * FROM incidents WHERE state<>'RESOLVED' ORDER BY id FOR UPDATE"
            )
            for incident in incidents:
                await self._opened(conn, incident)
                incident = await self._execution(conn, incident)
                agrees, reason = await self._agreement(conn, incident, since=since)
                agrees = agrees and reconciled
                if not reconciled:
                    reason = "RECONCILER_CYCLE_INCOMPLETE"
                pending = await rows(
                    conn,
                    "SELECT 1 FROM proposals WHERE incident_id=%s AND state IN "
                    "('CREATED','ALLOWED','NEEDS_OWNER','APPROVED','EXECUTING') LIMIT 1",
                    (incident["id"],),
                )
                if pending:
                    agrees, reason = False, "INCIDENT_HAS_PENDING_PROPOSAL"
                observation = await self._observation(conn, incident, reason)
                previous = await rows(
                    conn,
                    "SELECT details->>'observation' AS observation FROM audit_log "
                    "WHERE kind='STATE_TRANSITION' AND details->>'event'='INCIDENT_OBSERVED' "
                    "AND details->>'incident_id'=%s ORDER BY id DESC LIMIT 1",
                    (str(incident["id"]),),
                )
                changed = previous and previous[0]["observation"] != observation
                if agrees and incident["state"] in {"OPEN", "INVESTIGATING", "WAITING_EXTERNAL"}:
                    if incident["state"] == "OPEN":
                        incident = await self._move(conn, incident, E.INVESTIGATION_STARTED)
                    incident = await self._move(conn, incident, E.RECONCILED)
                    await Repositories(conn).incidents.update(
                        incident["id"], closed_at=self.clock()
                    )
                    await self._audit(conn, "INCIDENT_RESOLVED", incident, reason="RECONCILED")
                else:
                    if changed and not pending and incident["state"] == "WAITING_EXTERNAL":
                        incident = await self._move(conn, incident, E.EXTERNAL_UPDATE)
                    if not previous or changed:
                        await self._audit(conn, "RECONCILE_MISMATCH", incident, reason=reason)
                if not previous or changed:
                    await self._audit(
                        conn,
                        "STATE_TRANSITION",
                        incident,
                        event="INCIDENT_OBSERVED",
                        observation=observation,
                    )

    async def _observation(self, conn, incident, reason):
        subject = incident["subject"]
        state = {"reason": reason}
        # Ignore version and polling times: unchanged GETs must not trigger another model step.
        for key, table, columns in (
            (
                "dispute_id",
                "disputes",
                "status,phase,outcome_code,amount_refunded_minor,links,seller_response_due_date",
            ),
            ("attempt_id", "payout_attempts", "state,paypal_item_id"),
            ("run_id", "payout_runs", "state"),
        ):
            value = subject.get(key)
            if incident["kind"] == "RECONCILIATION":
                entity = {"dispute_id": "dispute", "attempt_id": "attempt"}.get(key)
                if entity and entity == subject.get("entity"):
                    value = subject.get("id")
            if identifier(value):
                # All identifiers here are private constants, never model input.
                from psycopg import sql

                cursor = await conn.execute(
                    sql.SQL("SELECT {} FROM {} WHERE id=%s").format(
                        sql.SQL(columns), sql.Identifier(table)
                    ),
                    (identifier(value),),
                )
                state[key] = await cursor.fetchone()
        return hashlib.sha256(json.dumps(state, sort_keys=True, default=str).encode()).hexdigest()


class LifecycleReconciler:
    """Composition hook: preserve the C15 poll result and worker scheduling interface."""

    def __init__(self, reconciler, incidents):
        self.reconciler, self.incidents = reconciler, incidents

    async def poll_once(self):
        since = self.incidents.clock()
        result = await self.reconciler.poll_once()
        if not result.busy:
            complete = not any(
                getattr(result, stage).errors
                for stage in ("attempts", "disputes", "balances", "backfill")
            )
            await self.incidents.sweep(since=since, reconciled=complete)
        return result
