"""GET-confirmed dispute mirrors and atomic maker-share protection.

No outcome codes are guessed: the production map remains the FSM's empty,
recording-owned default [pending D10]. Refund funding also remains unknown, so
this module never invents a cash journal from a dispute's refunded amount.
"""

from collections.abc import Mapping
from datetime import datetime

from countersign.db import Repositories, cas_transition
from countersign.fsm.dispute import KEEPS_RESERVE, Phase, phase
from countersign.fsm.obligation import OBLIGATION, ObligationEvent
from countersign.ledger.reservations import (
    consume_reservation,
    earmark_dispute_share,
    release_reservation,
)
from countersign.money import Money

from .cash_guard import defer_refund_cash
from .common import ReconcileError, StageResult, open_incident

_RESOLUTION = "DISPUTE_FINANCIAL_RESOLUTION"


def _date(value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ReconcileError("Invalid dispute timestamp")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ReconcileError("Invalid dispute timestamp")
    return parsed


async def _incident(conn, result, dispute, reason, *, before=None, after=None):
    result.incidents += await open_incident(
        conn, "dispute", dispute["id"], reason, before=before, after=after
    )


async def _transition(conn, obligation, event):
    state = await cas_transition(
        conn,
        OBLIGATION,
        "obligations",
        obligation["id"],
        obligation["state"],
        obligation["version"],
        event,
    )
    return {**obligation, "state": state, "version": obligation["version"] + 1}


async def _earmarks(conn, dispute_id):
    cursor = await conn.execute(
        "SELECT * FROM reservations WHERE dispute_id=%s AND kind='DISPUTE_EARMARK' "
        "AND state='ACTIVE' ORDER BY id FOR UPDATE",
        (dispute_id,),
    )
    return await cursor.fetchall()


async def _other_protection(conn, dispute, obligation):
    cursor = await conn.execute(
        "SELECT 1 FROM reservations WHERE obligation_id=%s AND kind='DISPUTE_EARMARK' "
        "AND state='ACTIVE' AND dispute_id IS DISTINCT FROM %s "
        "UNION ALL SELECT 1 FROM disputes WHERE order_id=%s AND id<>%s "
        "AND phase IN ('ACTIVE','UNKNOWN_OUTCOME') LIMIT 1",
        (obligation["id"], dispute["id"], obligation["order_id"], dispute["id"]),
    )
    return await cursor.fetchone() is not None


async def _marker(conn, dispute):
    cursor = await conn.execute(
        "SELECT details FROM audit_log WHERE kind='STATE_TRANSITION' "
        "AND details->>'event'=%s AND details->>'dispute_id'=%s "
        "ORDER BY id LIMIT 1",
        (_RESOLUTION, str(dispute["id"])),
    )
    row = await cursor.fetchone()
    return row["details"] if row else None


async def _protect(conn, repos, result, dispute, order, obligations):
    policy = await repos.policies.get("maker_share_pct")
    pct = policy["value"] if policy else None
    if type(pct) is not int or not 0 < pct <= 100:
        # Missing policy must still prevent an OPEN/SCHEDULED share being paid.
        for obligation in obligations:
            if obligation["state"] in {"OPEN", "SCHEDULED"}:
                await _transition(conn, obligation, ObligationEvent.DISPUTE_OPENED)
        await _incident(conn, result, dispute, "MAKER_SHARE_POLICY_UNAVAILABLE")
        return
    share = Money(order["amount_minor"], order["currency"].strip()).share_pct(pct)
    for obligation in obligations:
        if obligation["state"] in {"OPEN", "SCHEDULED", "RESERVED"}:
            if (obligation["amount_minor"], obligation["currency"].strip()) != (
                share.minor,
                share.currency,
            ):
                if obligation["state"] != "RESERVED":
                    await _transition(conn, obligation, ObligationEvent.DISPUTE_OPENED)
                await _incident(conn, result, dispute, "MAKER_SHARE_AMOUNT_MISMATCH")
                continue
            await earmark_dispute_share(
                conn,
                dispute_id=dispute["id"],
                obligation_id=obligation["id"],
                order_amount=Money(order["amount_minor"], order["currency"].strip()),
                maker_share_pct=pct,
            )
            if obligation["state"] != "RESERVED":
                await _transition(conn, obligation, ObligationEvent.DISPUTE_OPENED)
        elif obligation["state"] in {"IN_FLIGHT", "SETTLED"}:
            await _incident(
                conn, result, dispute, "PAID_BEFORE_DISPUTE", before=obligation["state"]
            )


async def _resolve(conn, repos, result, dispute, order, obligations):
    derived = Phase(dispute["phase"])
    previous = await _marker(conn, dispute)
    identity = {
        "event": _RESOLUTION,
        "dispute_id": str(dispute["id"]),
        "phase": str(derived),
        "amount_refunded_minor": dispute["amount_refunded_minor"],
        "currency": dispute["currency"].strip(),
    }
    if previous is not None:
        if any(previous.get(key) != value for key, value in identity.items()):
            # A later contradictory outcome must not leave the previously reopened
            # share available for payment while its adjustment is investigated.
            await _protect(conn, repos, result, dispute, order, obligations)
            await _incident(conn, result, dispute, "RESOLUTION_CHANGED_AFTER_POSTING")
        return
    marks = await _earmarks(conn, dispute["id"])
    if derived in {Phase.WON, Phase.CANCELLED}:
        for mark in marks:
            await release_reservation(conn, mark["id"])
        event = (
            ObligationEvent.DISPUTE_WON
            if derived == Phase.WON
            else ObligationEvent.DISPUTE_CANCELLED
        )
        for obligation in obligations:
            if obligation["state"] == "RESERVED" and not await _other_protection(
                conn, dispute, obligation
            ):
                await _transition(conn, obligation, event)
    else:
        if len(obligations) != 1:
            await _incident(conn, result, dispute, "REFUND_OBLIGATION_UNMATCHED")
            return
        obligation = obligations[0]
        if obligation["state"] in {"IN_FLIGHT", "SETTLED", "VOID"}:
            await _incident(
                conn, result, dispute, "REFUND_REQUIRES_OWNER", before=obligation["state"]
            )
            return
        if await _other_protection(conn, dispute, obligation):
            # Separate refunds on the same order cannot safely be added by guessing.
            await _incident(conn, result, dispute, "MULTIPLE_DISPUTE_RESOLUTIONS")
            return
        policy = await repos.policies.get("maker_share_pct")
        pct = policy["value"] if policy else None
        if type(pct) is not int or not 0 < pct <= 100:
            await _incident(conn, result, dispute, "MAKER_SHARE_POLICY_UNAVAILABLE")
            return
        order_amount = Money(order["amount_minor"], order["currency"].strip())
        expected = order_amount.share_pct(pct)
        if (obligation["amount_minor"], obligation["currency"].strip()) != (
            expected.minor,
            expected.currency,
        ):
            await _incident(conn, result, dispute, "MAKER_SHARE_AMOUNT_MISMATCH")
            return
        if derived == Phase.LOST_PARTIAL:
            refunded_share = Money(
                dispute["amount_refunded_minor"], dispute["currency"].strip()
            ).share_pct(pct)
            adjustments = await repos.obligations.find(
                {"order_id": order["id"], "maker_id": obligation["maker_id"], "kind": "ADJUSTMENT"}
            )
            if adjustments:
                await _incident(conn, result, dispute, "REFUND_ADJUSTMENT_CONFLICT")
                return
            if refunded_share.minor:
                adjustment = await repos.obligations.insert(
                    maker_id=obligation["maker_id"],
                    order_id=order["id"],
                    kind="ADJUSTMENT",
                    amount_minor=-refunded_share.minor,
                    currency=refunded_share.currency,
                    due_date=obligation["due_date"],
                    state="OPEN",
                )
                identity["adjustment_id"] = str(adjustment["id"])
                identity["adjustment_minor"] = -refunded_share.minor
        # A dropped opening webhook must not make a confirmed loss disappear.
        if obligation["state"] in {"OPEN", "SCHEDULED"}:
            obligation = await _transition(conn, obligation, ObligationEvent.DISPUTE_OPENED)
        event = (
            ObligationEvent.DISPUTE_LOST_FULL
            if derived == Phase.LOST_FULL
            else ObligationEvent.DISPUTE_LOST_PARTIAL
        )
        await _transition(conn, obligation, event)
        for mark in marks:
            await consume_reservation(conn, mark["id"])
        # [pending D10/F17]: amount_refunded does not identify the cash funding account.
        await _incident(conn, result, dispute, "REFUND_FUNDING_UNVERIFIED")
    await repos.audit_log.insert(actor="system", kind="STATE_TRANSITION", details=identity)


async def _one(conn, reader, original, result, now, outcome_map, *, detail=None, clock=None):
    repos = Repositories(conn)
    order = await repos.orders.get(original["order_id"], for_update=True)
    if order is None:
        raise ReconcileError("Dispute order unavailable")
    if detail is None:
        response = await reader.get_dispute(original["paypal_dispute_id"])
        if not 200 <= response.status_code < 300:
            raise ReconcileError("Dispute confirmation unavailable")
        detail = response.body
        if clock is not None:
            now = clock()  # Record when GET confirmed the refund, not cycle start.
    if not isinstance(detail, dict) or detail.get("dispute_id") != original["paypal_dispute_id"]:
        raise ReconcileError("Dispute identity mismatch")
    transactions = detail.get("disputed_transactions", [])
    if not isinstance(transactions, list) or any(not isinstance(t, dict) for t in transactions):
        raise ReconcileError("Dispute capture mismatch")
    # D8 confirmed this exact identity field; never match on an alternative guessed id.
    captures = {t.get("seller_transaction_id") for t in transactions}
    if captures != {order["paypal_capture_id"]} or order["paypal_capture_id"] is None:
        raise ReconcileError("Dispute capture mismatch")
    if not isinstance(detail.get("status"), str) or not detail["status"]:
        raise ReconcileError("Dispute status unavailable")
    outcome = detail.get("dispute_outcome") or {}
    if not isinstance(outcome, Mapping):
        raise ReconcileError("Dispute outcome unavailable")
    amount = refunded = None
    valid_amounts = True
    try:
        amount = Money.from_paypal(detail["dispute_amount"])
        if outcome.get("amount_refunded") is not None:
            refunded = Money.from_paypal(outcome["amount_refunded"])
        valid_amounts = (amount.minor, amount.currency) == (
            order["amount_minor"],
            order["currency"].strip(),
        ) and (refunded is None or refunded.currency == amount.currency)
    except (KeyError, TypeError, ValueError):
        valid_amounts = False
    derived = (
        phase(
            detail["status"],
            outcome.get("outcome_code"),
            refunded.minor if refunded else None,
            amount.minor,
            outcome_map=outcome_map,
        )
        if valid_amounts
        else Phase.UNKNOWN_OUTCOME
    )
    fields = {
        "status": detail["status"],
        "lifecycle_stage": detail.get("dispute_life_cycle_stage"),
        "reason": detail.get("reason"),
        "amount_minor": amount.minor if amount else None,
        "currency": amount.currency if amount else None,
        "seller_response_due_date": _date(detail.get("seller_response_due_date")),
        "outcome_code": outcome.get("outcome_code"),
        "amount_refunded_minor": refunded.minor if refunded else None,
        "phase": str(derived),
        "links": detail.get("links", []),
        "raw": detail,
        "last_synced_at": now,
    }
    # Do not invalidate a directly bound dispute approval on an identical read.
    changed = any(
        original.get(key) != value for key, value in fields.items() if key != "last_synced_at"
    )
    if changed:
        dispute = await repos.disputes.update(
            original["id"], expected_version=original["version"], **fields
        )
    else:
        await conn.execute(
            "UPDATE disputes SET last_synced_at=%s WHERE id=%s", (now, original["id"])
        )
        dispute = {**original, "last_synced_at": now}
    if valid_amounts and refunded is not None and refunded.minor > 0:
        await defer_refund_cash(conn, dispute, now=now)
    cursor = await conn.execute(
        "SELECT * FROM obligations WHERE order_id=%s AND kind='ORDER_SHARE' ORDER BY id FOR UPDATE",
        (order["id"],),
    )
    obligations = await cursor.fetchall()
    if not obligations:
        await _incident(conn, result, dispute, "ORDER_SHARE_UNAVAILABLE")
    if derived in KEEPS_RESERVE:
        await _protect(conn, repos, result, dispute, order, obligations)
        if derived == Phase.UNKNOWN_OUTCOME:
            result.unknown += 1
            await _incident(
                conn,
                result,
                dispute,
                "UNKNOWN_OUTCOME" if valid_amounts else "DISPUTE_AMOUNT_MISMATCH",
                after=derived,
            )
    else:
        if derived in {Phase.LOST_FULL, Phase.LOST_PARTIAL} and not await _marker(conn, dispute):
            # Protect before resolving: ambiguous partial/clawback cases retain the
            # earmark even if the dispute's opening delivery was completely lost.
            await _protect(conn, repos, result, dispute, order, obligations)
            cursor = await conn.execute(
                "SELECT * FROM obligations WHERE order_id=%s AND kind='ORDER_SHARE' "
                "ORDER BY id FOR UPDATE",
                (order["id"],),
            )
            obligations = await cursor.fetchall()
        await _resolve(conn, repos, result, dispute, order, obligations)
    result.updated += int(changed)


async def ingest_dispute(conn, detail, *, now: datetime, outcome_map=None) -> StageResult:
    """Adopt a trusted fresh GET detail; skip captures outside this marketplace.

    Used by discovery and optional event backfill. A list entry or webhook payload
    is never itself a confirmed detail. The caller owns the transaction and lock.
    """
    result = StageResult(seen=1)
    if not isinstance(detail, dict) or not isinstance(detail.get("dispute_id"), str):
        raise ReconcileError("Dispute identity unavailable")
    entries = detail.get("disputed_transactions", [])
    if not isinstance(entries, list) or any(not isinstance(t, dict) for t in entries):
        raise ReconcileError("Dispute capture unavailable")
    captures = {t.get("seller_transaction_id") for t in entries}
    if None in captures or len(captures) != 1:
        raise ReconcileError("Dispute capture unavailable")
    repos = Repositories(conn)
    orders = await repos.orders.find({"paypal_capture_id": captures.pop()})
    if not orders:
        result.skipped = 1
        return result
    if len(orders) != 1:
        raise ReconcileError("Dispute capture ambiguous")
    async with conn.transaction():
        existing = await repos.disputes.find({"paypal_dispute_id": detail["dispute_id"]})
        original = (
            existing[0]
            if existing
            else await repos.disputes.insert(
                paypal_dispute_id=detail["dispute_id"],
                order_id=orders[0]["id"],
                status="UNCONFIRMED",
                phase="ACTIVE",
            )
        )
        await _one(conn, None, original, result, now, outcome_map, detail=detail)
    return result


def _merge(result, item):
    for key in ("seen", "updated", "skipped", "incidents", "errors", "unknown"):
        setattr(result, key, getattr(result, key) + getattr(item, key))


async def _discover(conn, reader, result, now, outcome_map, clock=None):
    """List to discover identities, then GET details; follow every distinct next page."""
    if not callable(getattr(reader, "list_disputes", None)):
        return  # Small injected test readers can deliberately disable discovery.
    try:
        response = await reader.list_disputes(page_size=100)
        pages = set()
        discovered = set()
        while True:
            if not 200 <= response.status_code < 300 or not isinstance(response.body, dict):
                raise ReconcileError("Dispute discovery unavailable")
            items, links = response.body.get("items", []), response.body.get("links", [])
            if not isinstance(items, list) or not isinstance(links, list):
                raise ReconcileError("Dispute discovery unavailable")
            for summary in items:
                if not isinstance(summary, dict) or not isinstance(summary.get("dispute_id"), str):
                    raise ReconcileError("Dispute discovery unavailable")
                identifier = summary["dispute_id"]
                if identifier in discovered:
                    continue
                discovered.add(identifier)
                if await Repositories(conn).disputes.find({"paypal_dispute_id": identifier}):
                    continue  # The known-resource loop above already confirmed this one.
                try:
                    async with conn.transaction():
                        detail = await reader.get_dispute(identifier)
                        if not 200 <= detail.status_code < 300:
                            raise ReconcileError("Dispute discovery confirmation unavailable")
                        if (
                            not isinstance(detail.body, dict)
                            or detail.body.get("dispute_id") != identifier
                        ):
                            raise ReconcileError("Dispute discovery identity mismatch")
                        item = await ingest_dispute(
                            conn,
                            detail.body,
                            now=clock() if clock else now,
                            outcome_map=outcome_map,
                        )
                except Exception:  # noqa: BLE001 - isolate a failed newly discovered resource.
                    result.errors += 1
                else:
                    _merge(result, item)
            next_links = [
                link for link in links if isinstance(link, dict) and link.get("rel") == "next"
            ]
            if not next_links:
                return
            if len(next_links) != 1:
                raise ReconcileError("Dispute discovery pagination ambiguous")
            link = next_links[0]
            href = link.get("href")
            if (
                not isinstance(href, str)
                or link.get("method", "GET").upper() != "GET"
                or href in pages
            ):
                raise ReconcileError("Dispute discovery pagination unavailable")
            pages.add(href)
            response = await reader.follow(href)
    except Exception:  # noqa: BLE001 - known disputes still reconcile when listing is unavailable.
        result.errors += 1


async def reconcile_disputes(
    conn, reader, *, now: datetime, outcome_map=None, clock=None
) -> StageResult:
    """Run inside the caller's READ COMMITTED transaction and owner-decision lock.

    Include terminal mirrors with a surviving earmark: C14 may have observed their
    resolution before this poller gets its turn. Each dispute has its own savepoint.
    """
    result = StageResult()
    cursor = await conn.execute(
        "SELECT d.* FROM disputes d WHERE d.phase IN ('ACTIVE','UNKNOWN_OUTCOME') "
        "OR EXISTS (SELECT 1 FROM reservations r WHERE r.dispute_id=d.id "
        "AND r.kind='DISPUTE_EARMARK' AND r.state='ACTIVE') ORDER BY d.id FOR UPDATE OF d"
    )
    for original in await cursor.fetchall():
        result.seen += 1
        item = StageResult()
        try:
            async with conn.transaction():
                await _one(conn, reader, original, item, now, outcome_map, clock=clock)
        except Exception:  # noqa: BLE001 - savepoint rollback before a safe investigation.
            result.errors += 1
            await _incident(conn, result, original, "DISPUTE_CONFIRMATION_FAILED")
        else:
            for key in ("updated", "incidents", "unknown"):
                setattr(result, key, getattr(result, key) + getattr(item, key))
    await _discover(conn, reader, result, now, outcome_map, clock=clock)
    return result
