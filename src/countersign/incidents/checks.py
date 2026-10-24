"""Conservative agreement checks over the reconciler's committed GET mirrors."""

from uuid import UUID

from countersign.db import Repositories
from countersign.money import Money
from countersign.reconcile.attempts import _matches
from countersign.simulator.service import rows


def identifier(value):
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None


async def dispute_agrees(conn, dispute_id, *, since):
    repos = Repositories(conn)
    dispute = await repos.disputes.get(identifier(dispute_id)) if identifier(dispute_id) else None
    if not dispute or not dispute["last_synced_at"]:
        return False, "DISPUTE_GET_UNCONFIRMED"
    # C15 stops polling a resolved dispute once its earmarks are closed.
    # Its verified resolution remains usable until a later write invalidates it.
    later_writes = await rows(
        conn,
        "SELECT 1 FROM operations WHERE target_id=%s AND kind IN "
        "('DISPUTE_EVIDENCE','DISPUTE_ACCEPT_CLAIM') AND "
        "(state IN ('PENDING','IN_FLIGHT','UNKNOWN') OR submitted_at>%s) LIMIT 1",
        (dispute["id"], dispute["last_synced_at"]),
    )
    if later_writes:
        return False, "DISPUTE_WRITE_NOT_GET_CONFIRMED"
    raw = dispute["raw"] or {}
    if (
        raw.get("dispute_id") != dispute["paypal_dispute_id"]
        or raw.get("status") != "RESOLVED"
        or dispute["status"] != "RESOLVED"
        or dispute["phase"] not in {"WON", "CANCELLED", "LOST_FULL", "LOST_PARTIAL"}
    ):
        return False, "DISPUTE_NOT_RESOLVED"
    order = await repos.orders.get(dispute["order_id"]) if dispute["order_id"] else None
    transactions = raw.get("disputed_transactions", [])
    if not order or not any(
        t.get("seller_transaction_id") == order["paypal_capture_id"] for t in transactions
    ):
        return False, "DISPUTE_ORDER_UNMATCHED"
    try:
        amount = Money.from_paypal(raw.get("dispute_amount"))
    except (ValueError, TypeError, KeyError):
        return False, "DISPUTE_AMOUNT_UNMATCHED"
    if amount != Money(dispute["amount_minor"], dispute["currency"].strip()):
        return False, "DISPUTE_AMOUNT_UNMATCHED"
    outcome = raw.get("dispute_outcome")
    if not isinstance(outcome, dict) or outcome.get("outcome_code") != dispute["outcome_code"]:
        return False, "DISPUTE_GET_OUTCOME_MISMATCH"
    refunded = outcome.get("amount_refunded")
    try:
        confirmed_refund = Money.from_paypal(refunded) if refunded is not None else None
    except (ValueError, TypeError, KeyError):
        return False, "DISPUTE_GET_REFUND_MISMATCH"
    if (confirmed_refund is None and dispute["amount_refunded_minor"] is not None) or (
        confirmed_refund is not None
        and (
            confirmed_refund.currency != amount.currency
            or confirmed_refund.minor != dispute["amount_refunded_minor"]
        )
    ):
        return False, "DISPUTE_GET_REFUND_MISMATCH"
    marks = await repos.reservations.find({"dispute_id": dispute["id"], "state": "ACTIVE"})
    obligations = await repos.obligations.find({"order_id": order["id"], "kind": "ORDER_SHARE"})
    if marks or not obligations or any(o["state"] == "RESERVED" for o in obligations):
        return False, "DISPUTE_RESERVATIONS_OR_OBLIGATIONS_MISMATCH"
    policy = await repos.policies.get("maker_share_pct")
    pct = policy["value"] if policy else None
    if type(pct) is not int or not 0 < pct <= 100 or len(obligations) != 1:
        return False, "DISPUTE_OBLIGATION_POLICY_UNAVAILABLE"
    expected_share = Money(order["amount_minor"], order["currency"].strip()).share_pct(pct)
    if (
        obligations[0]["maker_id"] != order["maker_id"]
        or obligations[0]["currency"].strip() != expected_share.currency
        or obligations[0]["amount_minor"] != expected_share.minor
    ):
        return False, "DISPUTE_OBLIGATION_AMOUNT_MISMATCH"
    markers = await rows(
        conn,
        "SELECT details FROM audit_log WHERE kind='STATE_TRANSITION' "
        "AND details->>'event'='DISPUTE_FINANCIAL_RESOLUTION' "
        "AND details->>'dispute_id'=%s",
        (str(dispute["id"]),),
    )
    if len(markers) != 1:
        return False, "DISPUTE_RECONCILER_HAS_NOT_AGREED"
    marker = markers[0]["details"]
    if any(
        marker.get(k) != v
        for k, v in {
            "phase": dispute["phase"],
            "amount_refunded_minor": dispute["amount_refunded_minor"],
            "currency": dispute["currency"].strip(),
        }.items()
    ):
        return False, "DISPUTE_RESOLUTION_MARKER_MISMATCH"
    if dispute["phase"] == "LOST_FULL" and any(o["state"] != "VOID" for o in obligations):
        return False, "DISPUTE_LOSS_OBLIGATION_MISMATCH"
    if dispute["phase"] in {"WON", "CANCELLED", "LOST_PARTIAL"} and any(
        o["state"] == "VOID" for o in obligations
    ):
        return False, "DISPUTE_RELEASE_OBLIGATION_MISMATCH"
    if dispute["phase"] == "LOST_PARTIAL":
        adjustments = await repos.obligations.find({"order_id": order["id"], "kind": "ADJUSTMENT"})
        if (
            confirmed_refund is None
            or len(adjustments) != 1
            or str(adjustments[0]["id"]) != marker.get("adjustment_id")
            or adjustments[0]["amount_minor"] != marker.get("adjustment_minor")
            or adjustments[0]["maker_id"] != order["maker_id"]
            or adjustments[0]["currency"].strip() != amount.currency
            or adjustments[0]["amount_minor"] != -confirmed_refund.share_pct(pct).minor
        ):
            return False, "DISPUTE_ADJUSTMENT_MISMATCH"
    guards = await rows(
        conn,
        "SELECT 1 FROM policies WHERE key=%s AND value->>'pending'='true'",
        ("reconcile.refund:" + str(dispute["id"]),),
    )
    guard = await repos.policies.get("reconcile.refund:" + str(dispute["id"]))
    if guards or (
        dispute["phase"] in {"LOST_FULL", "LOST_PARTIAL"}
        and (not guard or guard["value"].get("pending") is not False)
    ):
        return False, "REFUND_CASH_NOT_RECONCILED"
    # A second unresolved dispute on the order keeps this case open conservatively.
    others = await rows(
        conn,
        "SELECT 1 FROM disputes WHERE order_id=%s AND id<>%s "
        "AND phase IN ('ACTIVE','UNKNOWN_OUTCOME')",
        (order["id"], dispute["id"]),
    )
    if others:
        return False, "ORDER_HAS_OTHER_UNRESOLVED_DISPUTE"
    return True, "RECONCILED"


async def payout_agrees(conn, attempt_id, *, since):
    repos = Repositories(conn)
    attempt = (
        await repos.payout_attempts.get(identifier(attempt_id)) if identifier(attempt_id) else None
    )
    if not attempt or not attempt["last_checked_at"] or attempt["last_checked_at"] < since:
        return False, "PAYOUT_GET_UNCONFIRMED"
    raw = attempt["paypal_raw"] or {}
    if (
        not attempt["paypal_item_id"]
        or not _matches(attempt, raw, item_id=attempt["paypal_item_id"])
        or raw.get("transaction_status") != attempt["state"]
    ):
        return False, "PAYOUT_GET_MISMATCH"
    expected = {
        "SUCCESS": ("PAID", "SETTLED", "PAYOUT_SUCCESS"),
        "RETURNED": ("UNPAID", "SCHEDULED", "PAYOUT_RETURNED"),
        "FAILED": ("UNPAID", "SCHEDULED", "PAYOUT_FAILED"),
    }.get(attempt["state"])
    if expected is None:
        return False, "PAYOUT_NOT_RECONCILED_TERMINAL"
    line = await repos.payout_lines.get(attempt["line_id"])
    linked = await rows(
        conn,
        "SELECT o.*,x.amount_minor AS allocated_minor FROM obligations o "
        "JOIN payout_line_obligations x ON o.id=x.obligation_id "
        "WHERE x.line_id=%s AND x.active",
        (attempt["line_id"],),
    )
    if (
        not line
        or line["state"] != expected[0]
        or not linked
        or line["amount_minor"] != attempt["amount_minor"]
        or line["currency"].strip() != attempt["currency"].strip()
        or sum(o["amount_minor"] for o in linked) != attempt["amount_minor"]
        or any(
            o["state"] != expected[1]
            or o["maker_id"] != line["maker_id"]
            or o["currency"].strip() != attempt["currency"].strip()
            or o["allocated_minor"] != o["amount_minor"]
            for o in linked
        )
    ):
        return False, "PAYOUT_OBLIGATIONS_MISMATCH"
    newer = await rows(
        conn,
        "SELECT 1 FROM payout_attempts WHERE line_id=%s AND attempt_no>%s",
        (line["id"], attempt["attempt_no"]),
    )
    if newer:
        return False, "PAYOUT_HAS_NEWER_ATTEMPT"
    marks = await rows(
        conn,
        "SELECT 1 FROM reservations WHERE state='ACTIVE' AND (run_id=%s OR obligation_id=ANY(%s))",
        (line["run_id"], [o["id"] for o in linked]),
    )
    if marks:
        return False, "PAYOUT_RESERVATIONS_MISMATCH"
    journals = await rows(
        conn, "SELECT memo FROM ledger_journals WHERE operation_id=%s", (attempt["operation_id"],)
    )
    events = {r["memo"].split(":", 1)[0] for r in journals}
    if not {"PAYOUT_SUBMITTED", expected[2]} <= events:
        return False, "PAYOUT_JOURNALS_MISMATCH"
    if attempt["state"] == "SUCCESS" and events & {"PAYOUT_RETURNED", "PAYOUT_FAILED"}:
        return False, "PAYOUT_JOURNAL_HISTORY_CONFLICT"
    return True, "RECONCILED"


async def run_agrees(conn, run_id, *, since):
    repos = Repositories(conn)
    run = await repos.payout_runs.get(identifier(run_id)) if identifier(run_id) else None
    if not run or run["state"] != "DONE":
        return False, "RUN_NOT_DONE"
    lines = await repos.payout_lines.find({"run_id": run["id"]})
    if not lines:
        return False, "RUN_HAS_NO_LINES"
    for line in lines:
        attempts = await rows(
            conn,
            "SELECT id FROM payout_attempts WHERE line_id=%s ORDER BY attempt_no DESC LIMIT 1",
            (line["id"],),
        )
        if not attempts:
            return False, "RUN_PAYOUT_UNCONFIRMED"
        agrees, reason = await payout_agrees(conn, attempts[0]["id"], since=since)
        if not agrees:
            return False, reason
    return True, "RECONCILED"
