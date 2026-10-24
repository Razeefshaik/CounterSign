"""The only code that sends money: the PLAN 2.3 executor protocol (HANDOFF 7).

1. Transaction A, under the owner-decisions lock: save one PENDING operation per payout,
   re-run every shield rule at execution with the bound approval, consume the approval,
   insert the attempts PREPARED and move the operations IN_FLIGHT. Commit.
2. Call PayPal outside any transaction, once per operation, never retried automatically.
3. Transaction B per operation: SUBMITTED, REJECTED or UNKNOWN, plus the ledger journal.

UNKNOWN is resolved only by an identical replay inside UNKNOWN_REPLAY_WINDOW, or by a
duplicate-key answer whose original batch GETs to the same payee, amount and keys. Past the
window the owner gets an incident of its own (DECISIONS, C17 handoff); nothing is resent.
"""

import dataclasses
from datetime import UTC, datetime, timedelta
from uuid import UUID

from countersign.approvals.service import ApprovalError, load_context
from countersign.db import Database, Repositories, cas_transition
from countersign.fsm.attempt import ATTEMPT, UNKNOWN_REPLAY_WINDOW, AttemptEvent
from countersign.fsm.line import LINE, LineEvent, LineState
from countersign.fsm.obligation import OBLIGATION, ObligationEvent
from countersign.fsm.operation import OPERATION, OperationEvent
from countersign.fsm.proposal import PROPOSAL, ProposalEvent
from countersign.ledger.journal import payout_submitted, post_journal, serialize_on
from countersign.ledger.reservations import consume_reservation, release_reservation
from countersign.money import Money, MoneyError
from countersign.paypal.http import (
    Ambiguous,
    DefiniteError,
    Ok,
    PayPalHTTP,
    canonical_payload_hash,
)
from countersign.paypal.read import PayPalRead
from countersign.paypal.write import PayPalWrite
from countersign.shield import snapshot as model
from countersign.shield.engine import Verdict, evaluate

from .actions import Actions

LOCK = "countersign:owner-decisions"  # shared with C18, so no owner decision interleaves
READY = ("ALLOWED", "APPROVED")
OPEN = ("PENDING", "IN_FLIGHT", "UNKNOWN")


class ExecutorError(RuntimeError):
    """A value-free reason the executor refused before changing anything."""


def _hash_bytes(payload) -> bytes:
    return bytes.fromhex(canonical_payload_hash(payload)[7:])


def _batch_id(body) -> str | None:
    header = body.get("batch_header") if isinstance(body, dict) else None
    value = header.get("payout_batch_id") if isinstance(header, dict) else None
    return value if isinstance(value, str) and value else None


def _money(row) -> Money:
    return Money(row["amount_minor"], row["currency"].strip())


class Executor(Actions):
    def __init__(
        self,
        database: Database,
        writer: PayPalWrite,
        reader: PayPalRead,
        *,
        clock=None,
        stale_after=timedelta(minutes=10),
    ):
        self.database, self.writer, self.reader = database, writer, reader
        self.clock = clock or (lambda: datetime.now(UTC))
        self.stale_after = stale_after

    @classmethod
    def from_http(cls, database: Database, http: PayPalHTTP, *, clock=None, **options):
        """Build the adapters here, so callers outside the executor never import the writer."""
        return cls(database, PayPalWrite(http), PayPalRead(http), clock=clock, **options)

    # -- execution ---------------------------------------------------------------------

    async def execute(self, proposal_id: UUID) -> dict:
        """Run one ALLOWED or APPROVED paying proposal; a second caller finds it taken."""
        for operation in await self._prepare(proposal_id):
            await self._record(operation["id"], await self._call(operation))
        return await self.status(proposal_id)

    async def status(self, proposal_id: UUID) -> dict:
        async with self.database.tx() as conn:
            repos = Repositories(conn)
            proposal = await repos.proposals.get(proposal_id)
            operations = await repos.operations.find({"proposal_id": proposal_id}, limit=1000)
        return {
            "proposal_state": proposal["state"] if proposal else None,
            "operations": {str(o["id"]): o["state"] for o in operations},
        }

    async def _prepare(self, proposal_id: UUID) -> list[dict]:
        if await self._defer_unconfigured(proposal_id):
            return []
        async with self.database.tx() as conn:
            await serialize_on(conn, LOCK)
            repos = Repositories(conn)
            row = await repos.proposals.get(proposal_id, for_update=True)
            if row is None:
                raise ExecutorError("PROPOSAL_NOT_FOUND")
            if row["state"] not in READY:
                return []  # already taken by another executor, or never executable
            try:
                action = model.Action(row["action"])
            except ValueError:
                raise ExecutorError("UNSUPPORTED_ACTION") from None
            if action not in model.PAYPAL_WRITES:
                raise ExecutorError("UNSUPPORTED_ACTION")
            await cas_transition(
                conn,
                PROPOSAL,
                "proposals",
                proposal_id,
                row["state"],
                row["version"],
                ProposalEvent.EXECUTION_STARTED,
            )
            cursor = await conn.execute(
                "SELECT * FROM approvals WHERE proposal_id=%s ORDER BY id FOR UPDATE",
                (proposal_id,),
            )
            approvals = await cursor.fetchall()
            now = self.clock()
            try:
                proposal, snap, payload = await load_context(
                    conn, row, now=now, behavior=self.writer.http.settings.behavior)
            except ApprovalError as exc:
                await self._block(conn, row, [], [f"context: {exc.code}"])
                return []
            kind = model.OPERATION_KIND[action]
            approval = approvals[0] if len(approvals) == 1 else None
            requests = payload["payouts"] if action in model.PAYING else [payload["request"]]
            if action not in model.PAYING:
                target = UUID(payload["request"]["target_id"])
                conflicts = [o for o in snap.operations if o.target_id == str(target)
                             and o.state in {"PENDING", "IN_FLIGHT"}]
                if conflicts:
                    await self._block(conn, row, [], ["target already has a write in flight"])
                    return []
            operations = [
                await repos.operations.insert(
                    kind=kind,
                    target_type="payout_line" if action in model.PAYING else body["target_type"],
                    target_id=UUID(body["line_id"] if action in model.PAYING else body["target_id"]),
                    payload=body,
                    payload_hash=_hash_bytes(body),
                    proposal_id=proposal_id,
                    approval_id=approval["id"] if approval else None,
                    state="PENDING",
                )
                for body in requests
            ]
            if not operations:
                await self._block(conn, row, [], ["no payout to send"])
                return []
            # The rows as stored must rebuild the approved payload byte for byte: that is
            # what WRITE_AHEAD and APPROVAL_BINDING then compare (DECISIONS, C19).
            stored = ({**payload, "payouts": [o["payload"] for o in operations]}
                      if action in model.PAYING else {**payload, "request": operations[0]["payload"]})
            previous = (row.get("verdict_details") or {}).get("operation_payload", {})
            if (action not in model.PAYING and "request" in previous
                    and canonical_payload_hash(previous) != canonical_payload_hash(stored)):
                await self._block(conn, row, operations, ["request changed since proposal"])
                return []
            states = {o["state"] for o in operations}
            decision = evaluate(
                proposal,
                dataclasses.replace(
                    snap,
                    at_execution=True,
                    operation=model.Operation(
                        str(operations[0]["id"]),
                        kind,
                        "PENDING" if states == {"PENDING"} else "BLOCKED",
                        canonical_payload_hash(stored),
                        str(proposal_id),
                    ),
                    approval=model.Approval(
                        str(approval["id"]),
                        str(approval["proposal_id"]),
                        "sha256:" + bytes(approval["payload_hash"]).hex(),
                        approval["record_versions"],
                        approval["expires_at"],
                        approval["used_at"],
                    )
                    if approval
                    else None,
                ),
            )
            await repos.audit_log.insert(
                actor="shield",
                kind="SHIELD_VERDICT",
                proposal_id=proposal_id,
                verdict=str(decision.verdict),
                details={"at_execution": True, "rule_results": decision.rule_results()},
            )
            owner_tier = model.TIER[action] is model.Tier.OWNER
            if decision.verdict is not Verdict.ALLOW or (
                owner_tier and not decision.approval_applied
            ):
                await self._block(conn, row, operations, [str(r) for r in decision.rules])
                return []
            if approval:
                cursor = await conn.execute(
                    "UPDATE approvals SET used_at=%s, used_by_operation_id=%s "
                    "WHERE id=%s AND used_at IS NULL RETURNING id",
                    (now, operations[0]["id"], approval["id"]),
                )
                if await cursor.fetchone() is None:
                    raise ExecutorError("APPROVAL_ALREADY_USED")  # rolls everything back
            run_id = row["params"].get("run_id")
            if action not in model.PAYING:
                await self._prepare_action(conn, operations[0], now)
                return [await repos.operations.get(operations[0]["id"])]
            if action is model.Action.SCHEDULE_PAYOUT_RUN:
                run = await repos.payout_runs.get(UUID(run_id), for_update=True)
                if run is None or run["state"] != "APPROVED":
                    raise ExecutorError("RUN_NOT_APPROVED")
                await repos.payout_runs.update(
                    run["id"], expected_version=run["version"], state="EXECUTING"
                )
            for operation, item in zip(operations, snap.payouts, strict=True):
                await self._prepare_attempt(conn, operation, item, snap, now)
            cursor = await conn.execute(
                "SELECT id FROM reservations WHERE state='ACTIVE' AND kind<>'DISPUTE_EARMARK' "
                "AND (proposal_id=%s OR run_id=%s) ORDER BY id FOR UPDATE",
                (proposal_id, UUID(run_id) if run_id else None),
            )
            # The submitted operations now count as outflows in the cash formula.
            for reservation in await cursor.fetchall():
                await consume_reservation(conn, reservation["id"])
            ids = [o["id"] for o in operations]
            cursor = await conn.execute(
                "SELECT * FROM operations WHERE id = ANY(%s) ORDER BY id", (ids,)
            )
            return await cursor.fetchall()

    async def _prepare_attempt(self, conn, operation, item, snap, now):
        repos = Repositories(conn)
        if operation["target_id"] != UUID(item.line_id):
            raise ExecutorError("PAYLOAD_ORDER_MISMATCH")
        line = snap.line_by_id[item.line_id]
        await repos.payout_attempts.insert(
            line_id=UUID(item.line_id),
            attempt_no=item.attempt_no,
            operation_id=operation["id"],
            sender_batch_id=item.sender_batch_id,
            request_id=item.request_id,
            sender_item_id=item.sender_item_id,
            payee_email=item.payee_email,
            amount_minor=item.amount.minor,
            currency=item.amount.currency,
            state="PREPARED",
            prepared_at=now,
        )
        line_event = (
            LineEvent.ATTEMPT_PREPARED
            if line.state is LineState.APPROVED
            else LineEvent.REPLACEMENT_APPROVED
        )
        await cas_transition(
            conn, LINE, "payout_lines", UUID(line.id), line.state, line.version, line_event
        )
        # First attempt or replacement, the line's obligations wait SCHEDULED.
        for obligation in snap.obligations:
            if obligation.line_id == item.line_id:
                await cas_transition(
                    conn,
                    OBLIGATION,
                    "obligations",
                    UUID(obligation.id),
                    obligation.state,
                    obligation.version,
                    ObligationEvent.ATTEMPT_PREPARED,
                )
        await cas_transition(
            conn,
            OPERATION,
            "operations",
            operation["id"],
            "PENDING",
            operation["version"],
            OperationEvent.LOCK_AND_RECHECK_OK,
        )
        await repos.operations.update(operation["id"], submitted_at=now)
        await repos.audit_log.insert(
            actor="executor",
            kind="OPERATION_PREPARED",
            proposal_id=operation["proposal_id"],
            operation_id=operation["id"],
            details={"line_id": item.line_id, "attempt_no": item.attempt_no},
        )

    async def _block(self, conn, row, operations, reasons):
        """Record a refusal at execution; nothing is sent and the run's money is freed."""
        repos = Repositories(conn)
        for operation in operations:
            await cas_transition(
                conn,
                OPERATION,
                "operations",
                operation["id"],
                operation["state"],
                operation["version"],
                OperationEvent.RECHECK_FAILED,
            )
            await repos.operations.update(operation["id"], error="; ".join(reasons)[:500])
        current = await repos.proposals.get(row["id"], for_update=True)
        await cas_transition(
            conn,
            PROPOSAL,
            "proposals",
            row["id"],
            current["state"],
            current["version"],
            ProposalEvent.RECHECK_FAILED,
        )
        run_id = (row["params"] or {}).get("run_id")
        cursor = await conn.execute(
            "SELECT id FROM reservations WHERE state='ACTIVE' AND kind<>'DISPUTE_EARMARK' "
            "AND (proposal_id=%s OR run_id=%s) ORDER BY id FOR UPDATE",
            (row["id"], UUID(run_id) if run_id else None),
        )
        for reservation in await cursor.fetchall():
            await release_reservation(conn, reservation["id"])
        if row["action"] == model.Action.SCHEDULE_PAYOUT_RUN and run_id:
            await self._release_run(conn, UUID(run_id))
        await repos.audit_log.insert(
            actor="executor",
            kind="APPROVAL_VOIDED",
            proposal_id=row["id"],
            details={"reasons": reasons},
        )

    async def _release_run(self, conn, run_id):
        repos = Repositories(conn)
        for line in await repos.payout_lines.find({"run_id": run_id}, limit=1000):
            if line["state"] != LineState.APPROVED:
                continue
            for link in await repos.payout_line_obligations.find(
                {"line_id": line["id"], "active": True}, limit=1000
            ):
                obligation = await repos.obligations.get(link["obligation_id"], for_update=True)
                if obligation["state"] == "SCHEDULED":
                    await cas_transition(
                        conn,
                        OBLIGATION,
                        "obligations",
                        obligation["id"],
                        obligation["state"],
                        obligation["version"],
                        ObligationEvent.LINE_RELEASED,
                    )
                await repos.payout_line_obligations.update(
                    (line["id"], obligation["id"]), active=False
                )
            await cas_transition(
                conn,
                LINE,
                "payout_lines",
                line["id"],
                line["state"],
                line["version"],
                LineEvent.RELEASED,
            )
        run = await repos.payout_runs.get(run_id, for_update=True)
        if run["state"] in ("APPROVED", "AWAITING_OWNER"):
            await repos.payout_runs.update(
                run_id, expected_version=run["version"], state="CANCELLED"
            )

    async def _call(self, operation, *, replay=False):
        try:
            method = {
                "PAYOUT_CREATE": self.writer.create_payout_single_item,
                "PAYOUT_CANCEL": self.writer.cancel_item,
                "DISPUTE_EVIDENCE": self.writer.provide_evidence,
                "DISPUTE_ACCEPT_CLAIM": self.writer.accept_claim,
            }[operation["kind"]]
            return await method(operation, replay_unknown=replay)
        except Exception as exc:  # noqa: BLE001 - unsure whether it was sent: fail closed
            return Ambiguous(type(exc).__name__)

    async def _record(self, operation_id: UUID, result) -> str:
        async with self.database.tx() as conn:
            operation = await Repositories(conn).operations.get(operation_id)
        if operation["kind"] != "PAYOUT_CREATE":
            return await self._record_action(operation, result)
        async with self.database.tx() as conn:
            repos = Repositories(conn)
            operation = await repos.operations.get(operation_id, for_update=True)
            if operation["state"] != "IN_FLIGHT":
                return operation["state"]  # a stale-flight sweep already took it
            [attempt] = await repos.payout_attempts.find({"operation_id": operation_id})
            attempt = await repos.payout_attempts.get(attempt["id"], for_update=True)
            now = self.clock()
            resp = getattr(result, "resp", None)
            if isinstance(result, Ok) and not _batch_id(resp.body):
                result = Ambiguous("Success without a batch id", resp)
            details = {
                "response": resp.safe_body if resp else None,
                "paypal_debug_id": resp.debug_id if resp else None,
            }
            if isinstance(result, Ok):
                await self._submitted(
                    conn, operation, attempt, _batch_id(resp.body), replay=False, now=now
                )
                await repos.operations.update(operation_id, completed_at=now, **details)
                kind = "PAYPAL_RESPONSE"
            elif isinstance(result, DefiniteError):
                await self._move(conn, ATTEMPT, "payout_attempts", attempt, "HTTP_DEFINITE_4XX")
                await self._move(conn, OPERATION, "operations", operation, "DEFINITE_ERROR")
                await repos.operations.update(
                    operation_id, completed_at=now, error="definite PayPal error", **details
                )
                await self._not_paid(conn, attempt["line_id"])
                kind = "PAYPAL_RESPONSE"
            else:
                await self._move(conn, ATTEMPT, "payout_attempts", attempt, "AMBIGUOUS")
                await self._move(conn, OPERATION, "operations", operation, "AMBIGUOUS")
                if result.original_batch_link:
                    details["response"] = {"original_batch_link": result.original_batch_link}
                await repos.operations.update(operation_id, error=result.reason, **details)
                kind = "OPERATION_UNKNOWN"
            await repos.audit_log.insert(
                actor="executor",
                kind=kind,
                proposal_id=operation["proposal_id"],
                operation_id=operation_id,
                details={"result": type(result).__name__},
            )
            await self._finish(conn, operation["proposal_id"])
            return (await repos.operations.get(operation_id))["state"]

    async def _move(self, conn, machine, table, row, event):
        await cas_transition(conn, machine, table, row["id"], row["state"], row["version"], event)

    async def _submitted(self, conn, operation, attempt, batch_id, *, replay, now):
        repos = Repositories(conn)
        await self._move(
            conn,
            ATTEMPT,
            "payout_attempts",
            attempt,
            AttemptEvent.REPLAY_CONFIRMED if replay else AttemptEvent.HTTP_2XX,
        )
        await repos.payout_attempts.update(
            attempt["id"], paypal_batch_id=batch_id, last_checked_at=now
        )
        await self._move(
            conn,
            OPERATION,
            "operations",
            operation,
            OperationEvent.RESOLVED_OK if replay else OperationEvent.OK,
        )
        await post_journal(
            conn, payout_submitted(_money(attempt)), operation_id=operation["id"], occurred_at=now
        )

    async def _not_paid(self, conn, line_id):
        """A definite rejection: the line is UNPAID and its obligations wait to be paid again."""
        repos = Repositories(conn)
        line = await repos.payout_lines.get(line_id, for_update=True)
        await self._move(conn, LINE, "payout_lines", line, LineEvent.ATTEMPT_NOT_PAID)
        for link in await repos.payout_line_obligations.find(
            {"line_id": line_id, "active": True}, limit=1000
        ):
            obligation = await repos.obligations.get(link["obligation_id"], for_update=True)
            await self._move(
                conn, OBLIGATION, "obligations", obligation, ObligationEvent.ATTEMPT_NOT_PAID
            )

    async def _finish(self, conn, proposal_id):
        """EXECUTED once every operation has a definite answer; UNKNOWN keeps it EXECUTING."""
        if proposal_id is None:
            return
        repos = Repositories(conn)
        operations = await repos.operations.find({"proposal_id": proposal_id}, limit=1000)
        states = {o["state"] for o in operations}
        if states & set(OPEN):
            return
        proposal = await repos.proposals.get(proposal_id, for_update=True)
        if proposal["state"] != "EXECUTING":
            return
        await self._move(
            conn,
            PROPOSAL,
            "proposals",
            proposal,
            ProposalEvent.EXECUTION_SUCCEEDED
            if "SUCCEEDED" in states
            else ProposalEvent.EXECUTION_FAILED,
        )
        run_id = (proposal["params"] or {}).get("run_id")
        if proposal["action"] == model.Action.SCHEDULE_PAYOUT_RUN and run_id:
            run = await repos.payout_runs.get(UUID(run_id), for_update=True)
            if run["state"] == "EXECUTING":
                await repos.payout_runs.update(
                    run["id"], expected_version=run["version"], state="DONE"
                )

    # -- UNKNOWN resolution ------------------------------------------------------------

    async def sweep(self) -> dict:
        """Turn abandoned IN_FLIGHT writes into UNKNOWN, then try to resolve every UNKNOWN."""
        recovered = await self.recover_stale()
        async with self.database.tx() as conn:
            cursor = await conn.execute(
                "SELECT id FROM operations WHERE state='UNKNOWN' "
                "ORDER BY id"
            )
            unknown = [r["id"] for r in await cursor.fetchall()]
        results = {str(i): await self.resolve_unknown(i) for i in unknown}
        return {"recovered": recovered, "resolved": results}

    async def recover_stale(self) -> int:
        """An IN_FLIGHT write with no recorded answer may or may not have reached PayPal."""
        cutoff = self.clock() - self.stale_after
        async with self.database.tx() as conn:
            cursor = await conn.execute(
                "SELECT * FROM operations WHERE state='IN_FLIGHT' "
                "AND submitted_at < %s ORDER BY id FOR UPDATE SKIP LOCKED",
                (cutoff,),
            )
            stale = await cursor.fetchall()
            repos = Repositories(conn)
            for operation in stale:
                if operation["kind"] == "PAYOUT_CREATE":
                    [attempt] = await repos.payout_attempts.find({"operation_id": operation["id"]})
                    await self._move(conn, ATTEMPT, "payout_attempts", attempt, "AMBIGUOUS")
                await self._move(conn, OPERATION, "operations", operation, "AMBIGUOUS")
                await repos.operations.update(
                    operation["id"], error="no recorded answer before the stale cutoff"
                )
                await repos.audit_log.insert(
                    actor="executor",
                    kind="OPERATION_UNKNOWN",
                    proposal_id=operation["proposal_id"],
                    operation_id=operation["id"],
                    details={"reason": "stale IN_FLIGHT"},
                )
            return len(stale)

    async def resolve_unknown(self, operation_id: UUID) -> str:
        async with self.database.tx() as conn:
            operation = await Repositories(conn).operations.get(operation_id)
        if operation and operation["kind"] != "PAYOUT_CREATE":
            return await self._resolve_action(operation)
        async with self.database.tx() as conn:
            repos = Repositories(conn)
            operation = await repos.operations.get(operation_id, for_update=True)
            if operation is None or operation["state"] != "UNKNOWN":
                return operation["state"] if operation else "MISSING"
            if operation["kind"] != "PAYOUT_CREATE":
                raise ExecutorError("UNSUPPORTED_ACTION")
            [attempt] = await repos.payout_attempts.find({"operation_id": operation_id})
            age = self.clock() - attempt["prepared_at"]
            if age > UNKNOWN_REPLAY_WINDOW:
                await self._owner_incident(conn, operation, attempt, age)
                return "UNKNOWN"
        result = await self._call(operation, replay=True)
        batch_id = _batch_id(result.resp.body) if isinstance(result, Ok) else None
        if isinstance(result, Ambiguous) and result.original_batch_link:
            batch_id = await self._confirm_original(result.original_batch_link, attempt)
        async with self.database.tx() as conn:
            repos = Repositories(conn)
            operation = await repos.operations.get(operation_id, for_update=True)
            if operation["state"] != "UNKNOWN":
                return operation["state"]
            attempt = await repos.payout_attempts.get(attempt["id"], for_update=True)
            now = self.clock()
            if batch_id:
                await self._submitted(conn, operation, attempt, batch_id, replay=True, now=now)
                await repos.operations.update(operation_id, completed_at=now, error=None)
                kind = "REPLAY_RESOLVED"
            else:
                # Still unknown: never a definite failure from a replay. Try again later.
                await repos.payout_attempts.update(attempt["id"], last_checked_at=now)
                await repos.operations.update(
                    operation_id, error=getattr(result, "reason", "replay not confirmed")
                )
                kind = "OPERATION_UNKNOWN"
            await repos.audit_log.insert(
                actor="executor",
                kind=kind,
                proposal_id=operation["proposal_id"],
                operation_id=operation_id,
                details={"replay": type(result).__name__, "confirmed": bool(batch_id)},
            )
            await self._finish(conn, operation["proposal_id"])
            return (await repos.operations.get(operation_id))["state"]

    async def _confirm_original(self, link, attempt) -> str | None:
        """Adopt a linked batch only if it is this attempt: same keys, payee and amount."""
        try:
            response = await self.reader.follow(link)
        except Exception:  # noqa: BLE001 - an unreadable link confirms nothing
            return None
        body = response.body if 200 <= response.status_code < 300 else None
        if not isinstance(body, dict):
            return None
        header = body.get("batch_header") or {}
        items = body.get("items")
        if (
            (header.get("sender_batch_header") or {}).get("sender_batch_id")
            != attempt["sender_batch_id"]
            or not isinstance(items, list)
            or len(items) != 1
        ):
            return None
        item = items[0].get("payout_item") if isinstance(items[0], dict) else None
        if not isinstance(item, dict):
            return None
        try:
            amount = Money.from_paypal(item.get("amount"))
        except (MoneyError, TypeError, KeyError):
            return None
        if (
            item.get("receiver") != attempt["payee_email"]
            or amount != _money(attempt)
            or items[0].get("sender_item_id", item.get("sender_item_id"))
            != attempt["sender_item_id"]
        ):
            return None
        return _batch_id(body)

    async def _owner_incident(self, conn, operation, attempt, age):
        """Past the window a replay could pay twice, so the owner decides; open it once."""
        repos = Repositories(conn)
        cursor = await conn.execute(
            "SELECT id FROM incidents WHERE kind='PAYOUT_ITEM' AND state<>'RESOLVED' "
            "AND subject->>'operation_id' = %s",
            (str(operation["id"]),),
        )
        if await cursor.fetchone():
            return
        incident = await repos.incidents.insert(
            kind="PAYOUT_ITEM",
            subject={
                "operation_id": str(operation["id"]),
                "attempt_id": str(attempt["id"]),
                "reason": "UNKNOWN_PAST_REPLAY_WINDOW",
                "age_days": age.days,
            },
            state="AWAITING_OWNER",
        )
        await repos.audit_log.insert(
            actor="executor",
            kind="INCIDENT_OPENED",
            proposal_id=operation["proposal_id"],
            operation_id=operation["id"],
            details={"incident_id": str(incident["id"]), "reason": "UNKNOWN_PAST_REPLAY_WINDOW"},
        )
