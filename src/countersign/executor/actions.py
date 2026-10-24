"""Non-idempotent writes: write once, resolve uncertainty only through resource GETs."""

from countersign.approvals.requests import relations
from countersign.db import Repositories
from countersign.fsm.operation import OPERATION
from countersign.ledger import journal
from countersign.ledger.reservations import (
    consume_reservation,
    create_reservation,
    release_reservation,
)
from countersign.money import Money
from countersign.paypal.http import DefiniteError, Ok, canonical_payload_hash
from countersign.paypal.write import _names
from countersign.reconcile.attempts import _matches, apply_confirmed_item

LOCK = "countersign:owner-decisions"


def _refund_amount(operation):
    value = operation["payload"]["refund_amount"]
    return Money(value["minor"], value["currency"])


def _evidence_present(body, expected):
    evidences = body.get("evidences", [])
    if not isinstance(evidences, list):
        return False
    wanted = expected["evidences"][0]
    for item in evidences:
        if not isinstance(item, dict) or item.get("evidence_type") != wanted["evidence_type"]:
            continue
        info = item.get("evidence_info")
        if (
            isinstance(info, dict)
            and info.get("tracking_info") == wanted["evidence_info"]["tracking_info"]
        ):
            return True
    return False


class Actions:
    """Shares the executor's shield, write-ahead, CAS and completion machinery."""

    async def _defer_unconfigured(self, proposal_id):
        # A separate administrative transaction, before financial Transaction A.
        # No proposal transition, operation or approval mutation occurs here.
        if self.writer.http.settings.behavior.refund_source is not None:
            return False
        async with self.database.tx() as conn:
            await journal.serialize_on(conn, LOCK)
            repos = Repositories(conn)
            row = await repos.proposals.get(proposal_id)
            if (
                not row
                or row["action"] != "ACCEPT_CLAIM"
                or row["state"] not in {"ALLOWED", "APPROVED"}
            ):
                return False
            cursor = await conn.execute(
                "SELECT id FROM audit_log WHERE proposal_id=%s "
                "AND details->>'reason'='REFUND_SOURCE_UNCONFIGURED' LIMIT 1",
                (proposal_id,),
            )
            if await cursor.fetchone() is None:
                await repos.audit_log.insert(
                    actor="executor",
                    kind="STATE_TRANSITION",
                    proposal_id=proposal_id,
                    details={"reason": "REFUND_SOURCE_UNCONFIGURED"},
                )
            return True

    async def _prepare_action(self, conn, operation, now):
        repos = Repositories(conn)
        metadata = {}
        if operation["kind"] == "DISPUTE_ACCEPT_CLAIM":
            # The funding source is an observed accounting policy, not an HTTP field.
            # Freeze it before POST so restart/reconfiguration cannot move the debit.
            metadata["refund_source"] = self.writer.http.settings.behavior.refund_source
            if metadata["refund_source"] == journal.AVAILABLE:
                await create_reservation(
                    conn,
                    "PROPOSAL_CASH",
                    _refund_amount(operation),
                    proposal_id=operation["proposal_id"],
                )
        await self._move(conn, OPERATION, "operations", operation, "LOCK_AND_RECHECK_OK")
        await repos.operations.update(operation["id"], submitted_at=now, response=metadata)
        await repos.audit_log.insert(
            actor="executor",
            kind="OPERATION_PREPARED",
            proposal_id=operation["proposal_id"],
            operation_id=operation["id"],
            details={"kind": operation["kind"], **metadata},
        )

    async def _record_action(self, original, result):
        async with self.database.tx() as conn:
            await journal.serialize_on(conn, LOCK)
            repos = Repositories(conn)
            operation = await repos.operations.get(original["id"], for_update=True)
            if operation["state"] != "IN_FLIGHT":
                return operation["state"]
            response = getattr(result, "resp", None)
            details = dict(operation["response"] or {})
            details["paypal"] = response.safe_body if response else None
            operation = await repos.operations.update(
                operation["id"],
                response=details,
                paypal_debug_id=response.debug_id if response else None,
            )
            cancel = operation["kind"] == "PAYOUT_CANCEL"
            already = cancel and response and "ITEM_ALREADY_CANCELLED" in _names(response.body)
            if isinstance(result, Ok) and not cancel:
                await self._action_succeeded(conn, operation, resolved=False)
            elif isinstance(result, DefiniteError) and not already:
                await self._close_refund_cash(conn, operation, spent=False)
                await self._move(conn, OPERATION, "operations", operation, "DEFINITE_ERROR")
                await repos.operations.update(
                    operation["id"], completed_at=self.clock(), error="definite PayPal error"
                )
            else:
                await self._move(conn, OPERATION, "operations", operation, "AMBIGUOUS")
                await repos.operations.update(operation["id"], error="resource GET required")
            await repos.audit_log.insert(
                actor="executor",
                kind="PAYPAL_RESPONSE",
                proposal_id=operation["proposal_id"],
                operation_id=operation["id"],
                details={"result": type(result).__name__},
            )
            await self._finish(conn, operation["proposal_id"])
            state = (await repos.operations.get(operation["id"]))["state"]
        return await self._resolve_action(original) if state == "UNKNOWN" else state

    async def _action_succeeded(self, conn, operation, *, resolved):
        repos = Repositories(conn)
        if operation["kind"] == "DISPUTE_ACCEPT_CLAIM":
            # Re-read the write-ahead metadata: recording the HTTP body never replaces it.
            current = await repos.operations.get(operation["id"])
            source = (current["response"] or {}).get("refund_source")
            await journal.post_journal(
                conn,
                journal.refund(_refund_amount(operation), source=source),
                operation_id=operation["id"],
                occurred_at=self.clock(),
            )
            await self._close_refund_cash(conn, operation, spent=True)
        await self._move(
            conn, OPERATION, "operations", operation, "RESOLVED_OK" if resolved else "OK"
        )
        await repos.operations.update(operation["id"], completed_at=self.clock(), error=None)

    async def _close_refund_cash(self, conn, operation, *, spent):
        if operation["kind"] != "DISPUTE_ACCEPT_CLAIM":
            return
        reservations = await Repositories(conn).reservations.find(
            {"proposal_id": operation["proposal_id"], "kind": "PROPOSAL_CASH", "state": "ACTIVE"}
        )
        for reservation in reservations:
            await (consume_reservation if spent else release_reservation)(conn, reservation["id"])

    async def _resolve_action(self, original):
        # Re-read after recording/another executor; never issue a second POST here.
        async with self.database.tx() as conn:
            repos = Repositories(conn)
            operation = await repos.operations.get(original["id"])
            if operation["state"] != "UNKNOWN":
                return operation["state"]
            attempt = (
                await repos.payout_attempts.get(operation["target_id"])
                if operation["kind"] == "PAYOUT_CANCEL"
                else None
            )
        payload = operation["payload"]
        body, confirmed = None, False
        try:
            if bytes(operation["payload_hash"]).hex() != canonical_payload_hash(payload)[7:]:
                raise ValueError("changed persisted payload")
            response = (
                await self.reader.get_payout_item(payload["paypal_item_id"])
                if attempt
                else await self.reader.get_dispute(payload["paypal_dispute_id"])
            )
            body = response.body if 200 <= response.status_code < 300 else None
            if isinstance(body, dict):
                if attempt:
                    confirmed = body.get("transaction_status") == "RETURNED" and _matches(
                        attempt, body, item_id=payload["paypal_item_id"]
                    )
                elif body.get("dispute_id") == payload["paypal_dispute_id"]:
                    if operation["kind"] == "DISPUTE_EVIDENCE":
                        before = payload["observation"]
                        changed_status = (
                            isinstance(body.get("status"), str)
                            and body["status"] != before["status"]
                        )
                        removed = (
                            isinstance(body.get("links"), list)
                            and "provide_evidence" in before["relations"]
                            and "provide_evidence" not in relations(body["links"])
                        )
                        confirmed = (
                            _evidence_present(body, payload["body"]) or changed_status or removed
                        )
                    else:
                        outcome = body.get("dispute_outcome") or {}
                        confirmed = body.get("status") == "RESOLVED" and Money.from_paypal(
                            outcome.get("amount_refunded")
                        ) == _refund_amount(operation)
        except Exception:  # noqa: BLE001 - unreadable or incomplete GET proves nothing
            confirmed = False
        async with self.database.tx() as conn:
            await journal.serialize_on(conn, LOCK)
            repos = Repositories(conn)
            current = await repos.operations.get(operation["id"], for_update=True)
            if current["state"] != "UNKNOWN":
                return current["state"]
            if confirmed:
                if attempt:
                    attempt = await repos.payout_attempts.get(attempt["id"], for_update=True)
                    # Apply the same financial/FSM code as ordinary reconciliation.
                    await apply_confirmed_item(
                        conn, attempt, payload["paypal_item_id"], body, self.clock()
                    )
                    line = await repos.payout_lines.get(attempt["line_id"])
                    if line["state"] != "UNPAID":
                        confirmed = False
                if confirmed:
                    await self._action_succeeded(conn, current, resolved=True)
                    await repos.audit_log.insert(
                        actor="executor",
                        kind="REPLAY_RESOLVED",
                        proposal_id=current["proposal_id"],
                        operation_id=current["id"],
                        details={"method": "GET"},
                    )
                    await self._finish(conn, current["proposal_id"])
            if not confirmed:
                await self._action_incident(conn, current)
            return (await repos.operations.get(current["id"]))["state"]

    async def _action_incident(self, conn, operation):
        cursor = await conn.execute(
            "SELECT id FROM incidents WHERE state<>'RESOLVED' "
            "AND subject->>'operation_id'=%s LIMIT 1",
            (str(operation["id"]),),
        )
        if await cursor.fetchone():
            return
        repos = Repositories(conn)
        incident = await repos.incidents.insert(
            kind="PAYOUT_ITEM" if operation["kind"] == "PAYOUT_CANCEL" else "DISPUTE",
            subject={
                "operation_id": str(operation["id"]),
                "reason": "NON_IDEMPOTENT_WRITE_UNCONFIRMED",
                "target_id": str(operation["target_id"]),
            },
            state="AWAITING_OWNER",
        )
        await repos.audit_log.insert(
            actor="executor",
            kind="INCIDENT_OPENED",
            proposal_id=operation["proposal_id"],
            operation_id=operation["id"],
            details={
                "incident_id": str(incident["id"]),
                "reason": "NON_IDEMPOTENT_WRITE_UNCONFIRMED",
            },
        )
