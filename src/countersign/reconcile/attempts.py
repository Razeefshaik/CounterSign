"""GET-confirm payout items and reconcile their domain states and journals atomically.

Attempt states mirror even surprising known PayPal states. Domain FSMs remain strict:
an observed regression never makes a settled obligation payable a second time.
"""

from collections.abc import Mapping
from datetime import datetime

from countersign.db import Repositories, cas_transition
from countersign.fsm.attempt import LIVE, PAYPAL_STATUSES, mirror
from countersign.fsm.line import LINE, LineEvent
from countersign.fsm.obligation import OBLIGATION, ObligationEvent
from countersign.ledger import journal
from countersign.money import Money

from .common import ReconcileError, StageResult, open_incident


def _body(response):
    if not 200 <= response.status_code < 300 or not isinstance(response.body, Mapping):
        raise ReconcileError("Payout confirmation unavailable")
    return dict(response.body)


def _matches(attempt, detail, *, item_id=None):
    item = detail.get("payout_item")
    if not isinstance(item, Mapping):
        return False
    try:
        amount = Money.from_paypal(item.get("amount"))
    except (ValueError, TypeError, KeyError):
        return False
    return (
        detail.get("payout_batch_id") == attempt["paypal_batch_id"]
        and (item_id is None or detail.get("payout_item_id") == item_id)
        and item.get("sender_item_id") == attempt["sender_item_id"]
        and item.get("receiver") == attempt["payee_email"]
        and amount == Money(attempt["amount_minor"], attempt["currency"].strip())
        and detail.get("sender_batch_id", attempt["sender_batch_id"]) == attempt["sender_batch_id"]
    )


async def _confirmed_item(reader, attempt):
    item_id = attempt["paypal_item_id"]
    if not attempt["paypal_batch_id"]:
        raise ReconcileError("Payout identity missing")
    if item_id is None:
        body = _body(await reader.get_payout_batch(attempt["paypal_batch_id"]))
        header = body.get("batch_header") or {}
        sender = header.get("sender_batch_header") or {}
        items = body.get("items")
        if (
            header.get("payout_batch_id") != attempt["paypal_batch_id"]
            or sender.get("sender_batch_id") != attempt["sender_batch_id"]
            or not isinstance(items, list)
            or len(items) != 1
            or not _matches(attempt, items[0])
            or not isinstance(items[0].get("payout_item_id"), str)
        ):
            raise ReconcileError("Payout batch identity mismatch")
        item_id = items[0]["payout_item_id"]
    detail = _body(await reader.get_payout_item(item_id))
    if not _matches(attempt, detail, item_id=item_id):
        raise ReconcileError("Payout item identity mismatch")
    return item_id, detail


async def _events(conn, operation_id):
    cursor = await conn.execute(
        "SELECT memo FROM ledger_journals WHERE operation_id=%s ORDER BY id",
        (operation_id,),
    )
    return {row["memo"].split(":", 1)[0] for row in await cursor.fetchall()}


def _move(event, amount, debit, credit, memo):
    return journal.JournalDraft(
        event, memo, (journal.Posting(debit, amount), journal.Posting(credit, -amount))
    )


async def _financial(conn, attempt, line, obligations, new_state, now, result):
    """Posting history, not a mirrored regression, determines each money move's source."""
    events = await _events(conn, attempt["operation_id"])
    if journal.Event.PAYOUT_SUBMITTED not in events:
        # Executor Tx2 must commit the submitted outflow with the SUBMITTED attempt.
        # C15 does not infer missing cash movements from an item GET.
        result.incidents += await open_incident(
            conn, "attempt", attempt["id"], "PAYOUT_SUBMISSION_JOURNAL_MISSING"
        )
        return
    amount = Money(attempt["amount_minor"], attempt["currency"].strip())
    settled = journal.Event.PAYOUT_SUCCESS in events
    returned = journal.Event.PAYOUT_RETURNED in events or journal.Event.PAYOUT_FAILED in events
    unclaimed = journal.Event.PAYOUT_UNCLAIMED in events
    source = journal.UNCLAIMED if unclaimed else journal.IN_TRANSIT
    draft = None
    line_event = obligation_event = None
    if new_state == "SUCCESS" and not settled and not returned:
        if line["state"] != "PAYING" or any(o["state"] != "IN_FLIGHT" for o in obligations):
            result.incidents += await open_incident(
                conn, "attempt", attempt["id"], "SETTLEMENT_STATES_DO_NOT_AGREE"
            )
            return
        draft = _move(
            journal.Event.PAYOUT_SUCCESS,
            amount,
            journal.maker_payable(line["maker_id"]),
            source,
            "payout settled",
        )
        line_event, obligation_event = LineEvent.ATTEMPT_PAID, ObligationEvent.ATTEMPT_PAID
    elif new_state == "UNCLAIMED" and not (settled or returned or unclaimed):
        # Whether available excludes unclaimed money remains [pending D4]. C16 default.
        draft = journal.payout_unclaimed(amount)
    elif new_state in {"RETURNED", "FAILED", "REFUNDED", "REVERSED"} and not returned:
        if settled:
            if new_state not in {"REFUNDED", "REVERSED"}:
                result.incidents += await open_incident(
                    conn, "attempt", attempt["id"], "UNEXPECTED_RETURN_AFTER_SETTLEMENT"
                )
                return
            if any(o["state"] != "SETTLED" for o in obligations):
                result.incidents += await open_incident(
                    conn, "attempt", attempt["id"], "CLAWBACK_STATES_DO_NOT_AGREE"
                )
                return
            draft = _move(
                journal.Event.PAYOUT_RETURNED,
                amount,
                journal.AVAILABLE,
                journal.maker_payable(line["maker_id"]),
                "settled payout clawed back",
            )
            obligation_event = ObligationEvent.PAYOUT_CLAWED_BACK
            # C10 has no PAID -> UNPAID line event. Keep PAID and request owner review.
            result.incidents += await open_incident(
                conn,
                "attempt",
                attempt["id"],
                "SETTLED_PAYOUT_CLAWBACK_NEEDS_LINE_REVIEW",
                before="PAID",
                after=new_state,
            )
        else:
            if line["state"] != "PAYING" or any(o["state"] != "IN_FLIGHT" for o in obligations):
                result.incidents += await open_incident(
                    conn, "attempt", attempt["id"], "RETURN_STATES_DO_NOT_AGREE"
                )
                return
            # RETURNED is the conservative cancel result [pending D5]. REFUNDED/REVERSED
            # observed before a SUCCESS can still confirm the original money is back.
            event = (
                journal.Event.PAYOUT_FAILED
                if new_state == "FAILED"
                else journal.Event.PAYOUT_RETURNED
            )
            draft = _move(event, amount, journal.AVAILABLE, source, "payout funds returned")
            line_event, obligation_event = (
                LineEvent.ATTEMPT_NOT_PAID,
                ObligationEvent.ATTEMPT_NOT_PAID,
            )
    if draft is not None:
        await journal.post_journal(
            conn, draft, operation_id=attempt["operation_id"], occurred_at=now
        )
    if line_event is not None:
        await cas_transition(
            conn, LINE, "payout_lines", line["id"], line["state"], line["version"], line_event
        )
    if obligation_event is not None:
        for obligation in obligations:
            await cas_transition(
                conn,
                OBLIGATION,
                "obligations",
                obligation["id"],
                obligation["state"],
                obligation["version"],
                obligation_event,
            )
        if obligation_event == ObligationEvent.PAYOUT_CLAWED_BACK:
            await conn.execute(
                "UPDATE payout_line_obligations SET active=false WHERE line_id=%s AND active",
                (line["id"],),
            )


async def _one(conn, reader, attempt, now):
    result = StageResult(seen=1)
    repos = Repositories(conn)
    item_id, detail = await _confirmed_item(reader, attempt)
    status = detail.get("transaction_status")
    if status not in PAYPAL_STATUSES:
        await repos.payout_attempts.update(
            attempt["id"],
            expected_version=attempt["version"],
            paypal_raw=detail,
            last_checked_at=now,
        )
        result.incidents += await open_incident(
            conn,
            "attempt",
            attempt["id"],
            "UNKNOWN_PAYPAL_PAYOUT_STATUS",
            before=attempt["state"],
            after="UNKNOWN_PAYPAL_STATUS",
        )
        result.updated = 1
        return result
    new_state, expected = mirror(attempt["state"], status)
    if not expected:
        result.incidents += await open_incident(
            conn,
            "attempt",
            attempt["id"],
            "UNEXPECTED_PAYOUT_TRANSITION",
            before=attempt["state"],
            after=str(new_state),
        )
    cursor = await conn.execute(
        "SELECT id,state FROM payout_attempts WHERE line_id=%s ORDER BY attempt_no DESC",
        (attempt["line_id"],),
    )
    siblings = await cursor.fetchall()
    latest = siblings[0]["id"] == attempt["id"]
    if (
        not latest
        and new_state in LIVE
        and any(row["id"] != attempt["id"] and row["state"] in LIVE for row in siblings)
    ):
        # The live-attempt index forbids two live states, even for a surprising GET.
        # Preserve the observation verbatim without weakening that database safety gate.
        await repos.payout_attempts.update(
            attempt["id"],
            expected_version=attempt["version"],
            paypal_raw=detail,
            last_checked_at=now,
        )
        result.incidents += await open_incident(
            conn,
            "attempt",
            attempt["id"],
            "OLDER_ATTEMPT_CONFLICTS_WITH_LIVE_REPLACEMENT",
            before=attempt["state"],
            after=str(new_state),
        )
        result.updated = 1
        return result
    line = await repos.payout_lines.get(attempt["line_id"], for_update=True)
    cursor = await conn.execute(
        "SELECT o.*,x.amount_minor AS allocated_minor FROM obligations o JOIN payout_line_obligations x "
        "ON x.obligation_id=o.id WHERE x.line_id=%s AND x.active ORDER BY o.id FOR UPDATE OF o",
        (line["id"],),
    )
    obligations = await cursor.fetchall()
    if not latest:
        if str(new_state) != attempt["state"]:
            result.incidents += await open_incident(
                conn,
                "attempt",
                attempt["id"],
                "OLDER_ATTEMPT_CHANGED_AFTER_REPLACEMENT",
                before=attempt["state"],
                after=str(new_state),
            )
    elif not obligations:
        already_clawed_back = new_state in {"REFUNDED", "REVERSED"} and (
            journal.Event.PAYOUT_RETURNED in await _events(conn, attempt["operation_id"])
        )
        if not already_clawed_back:
            result.incidents += await open_incident(
                conn, "attempt", attempt["id"], "PAYOUT_HAS_NO_ACTIVE_OBLIGATIONS"
            )
    elif (
        line["amount_minor"] != attempt["amount_minor"]
        or line["currency"].strip() != attempt["currency"].strip()
        or sum(o["allocated_minor"] for o in obligations) != attempt["amount_minor"]
        or any(
            o["maker_id"] != line["maker_id"]
            or o["currency"].strip() != attempt["currency"].strip()
            or o["allocated_minor"] != o["amount_minor"]
            for o in obligations
        )
    ):
        result.incidents += await open_incident(
            conn, "attempt", attempt["id"], "PAYOUT_OBLIGATION_ALLOCATION_MISMATCH"
        )
    else:
        await _financial(conn, attempt, line, obligations, new_state, now, result)
    changed = str(new_state) != attempt["state"] or item_id != attempt["paypal_item_id"]
    await repos.payout_attempts.update(
        attempt["id"],
        expected_version=attempt["version"],
        state=str(new_state),
        paypal_item_id=item_id,
        paypal_raw=detail,
        last_checked_at=now,
    )
    result.updated = int(changed)
    result.skipped = int(not changed)
    return result


async def reconcile_attempts(conn, reader, *, now: datetime) -> StageResult:
    """Reconcile inside the cycle's locked transaction; isolate failures by savepoint."""
    result = StageResult()
    cursor = await conn.execute("SELECT * FROM payout_attempts ORDER BY id FOR UPDATE")
    for attempt in await cursor.fetchall():
        if attempt["state"] in {"UNKNOWN", "PREPARED"}:
            result.unknown += int(attempt["state"] == "UNKNOWN")
            result.prepared += int(attempt["state"] == "PREPARED")
            result.skipped += 1
            continue
        if attempt["state"] == "REJECTED":
            result.skipped += 1
            continue
        try:
            async with conn.transaction():
                item_result = await _one(conn, reader, attempt, now)
        except Exception:  # noqa: BLE001 - rollback financial changes; retain only a safe incident.
            result.seen += 1
            result.errors += 1
            result.incidents += await open_incident(
                conn, "attempt", attempt["id"], "PAYOUT_CONFIRMATION_OR_ATOMIC_UPDATE_FAILED"
            )
        else:
            for field in ("seen", "updated", "skipped", "incidents", "errors"):
                setattr(result, field, getattr(result, field) + getattr(item_result, field))
    return result
