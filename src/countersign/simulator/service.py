"""Counterfactual payouts over a repeatable, read-only PostgreSQL snapshot.

Ignoring dispute earmarks changes which obligations are paid, never the cash
formula: PayPal available already excludes withheld cash. No writes or PayPal calls.
"""

from collections import defaultdict
from datetime import timedelta
from uuid import UUID

from countersign.ledger.cash import BalanceView, Outflow, Reservation, cash_position
from countersign.money import Money


class SimulationError(ValueError):
    """A fixed public error without database or request values."""


def money(minor, currency):
    return {"minor": minor, "currency": currency}


async def rows(conn, statement, params=()):
    cursor = await conn.execute(statement, params)
    return await cursor.fetchall()


async def run_replacement_entries(conn, run):
    """Read the owner-bound manifest, never an unrelated later agent proposal."""
    if run["approval_id"] is not None:
        manifests = await rows(
            conn,
            "SELECT p.params FROM proposals p JOIN approvals a ON a.proposal_id=p.id "
            "WHERE a.id=%s AND p.action='SCHEDULE_PAYOUT_RUN' AND p.params->>'run_id'=%s",
            (run["approval_id"], str(run["id"])),
        )
    else:
        manifests = await rows(
            conn,
            "SELECT params FROM proposals WHERE action='SCHEDULE_PAYOUT_RUN' "
            "AND model='deterministic-planner' AND params->>'run_id'=%s "
            "AND state IN ('CREATED','NEEDS_OWNER')",
            (str(run["id"]),),
        )
    if not manifests:
        if run["approval_id"] is not None:
            raise SimulationError("RUN_MANIFEST_UNAVAILABLE")
        return {}
    if len(manifests) != 1:
        raise SimulationError("RUN_MANIFEST_UNAVAILABLE")
    replacements = manifests[0]["params"].get("replacements", [])
    if not isinstance(replacements, list):
        raise SimulationError("RUN_MANIFEST_UNAVAILABLE")
    result = {}
    for entry in replacements:
        try:
            line_id, attempt_no = UUID(entry["line_id"]), entry["attempt_no"]
        except (KeyError, ValueError, TypeError, AttributeError):
            raise SimulationError("RUN_MANIFEST_UNAVAILABLE") from None
        if line_id in result or type(attempt_no) is not int or attempt_no < 2:
            raise SimulationError("RUN_MANIFEST_UNAVAILABLE")
        result[line_id] = attempt_no
    return result


async def run_replacements(conn, run):
    return list(await run_replacement_entries(conn, run))


async def simulate_run(conn, run_id, *, respect_reserves=True):
    runs = await rows(conn, "SELECT * FROM payout_runs WHERE id=%s", (run_id,))
    if not runs:
        raise SimulationError("RUN_NOT_FOUND")
    run = runs[0]
    if run["state"] not in {"DRAFT", "AWAITING_OWNER", "APPROVED"}:
        return {"available": False, "reason": "RUN_ALREADY_EXECUTING_OR_FINISHED"}
    replacements = await run_replacements(conn, run)
    obligations = await rows(
        conn,
        "SELECT o.*,x.line_id,l.run_id FROM obligations o "
        "LEFT JOIN payout_line_obligations x ON x.obligation_id=o.id AND x.active "
        "LEFT JOIN payout_lines l ON l.id=x.line_id "
        "WHERE o.due_date<=%s AND o.state IN ('OPEN','RESERVED','SCHEDULED') "
        "AND (x.line_id IS NULL OR l.run_id=%s OR l.id=ANY(%s)) ORDER BY o.id",
        (run["week_start"] + timedelta(days=6), run_id, replacements),
    )
    if not obligations:
        return {"available": False, "reason": "NO_OUTSTANDING_RUN_OBLIGATIONS"}
    currencies = {r["currency"].strip() for r in obligations}
    if len(currencies) != 1:
        raise SimulationError("MIXED_CURRENCY_RUN")
    currency = currencies.pop()
    balances = await rows(
        conn,
        "SELECT * FROM balance_snapshots WHERE currency=%s "
        "ORDER BY fetched_at DESC,id DESC LIMIT 1",
        (currency,),
    )
    guarded = await rows(
        conn,
        "SELECT 1 FROM policies WHERE starts_with(key,'reconcile.refund:') "
        "AND value->>'pending'='true' AND value->>'currency'=%s LIMIT 1",
        (currency,),
    )
    if (
        guarded
        or not balances
        or balances[0]["available_minor"] is None
        or balances[0]["last_refresh_time"] is None
    ):
        return {"available": False, "reason": "CASH_UNKNOWN"}
    balance = balances[0]
    reservations = await rows(
        conn, "SELECT * FROM reservations WHERE state='ACTIVE' AND currency=%s", (currency,)
    )
    disputes = await rows(
        conn, "SELECT id,order_id FROM disputes WHERE phase IN ('ACTIVE','UNKNOWN_OUTCOME')"
    )
    protected_orders = {r["order_id"] for r in disputes}
    protected_ids = {r["obligation_id"] for r in reservations if r["kind"] == "DISPUTE_EARMARK"}
    grouped = defaultdict(int)
    withheld = 0
    for obligation in obligations:
        protected = (
            obligation["state"] == "RESERVED"
            or obligation["order_id"] in protected_orders
            or obligation["id"] in protected_ids
        )
        if protected:
            withheld += obligation["amount_minor"]
        if not respect_reserves or not protected:
            grouped[obligation["maker_id"]] += obligation["amount_minor"]
    payout = sum(max(0, amount) for amount in grouped.values())
    outflow_rows = await rows(
        conn,
        "SELECT a.amount_minor,op.submitted_at FROM payout_attempts a JOIN operations op "
        "ON op.id=a.operation_id WHERE op.kind='PAYOUT_CREATE' AND op.submitted_at IS NOT NULL "
        "AND a.currency=%s UNION ALL "
        "SELECT -p.amount_minor,j.occurred_at FROM ledger_postings p JOIN ledger_journals j "
        "ON j.id=p.journal_id WHERE p.account='paypal:available' AND p.amount_minor<0 "
        "AND p.currency=%s AND j.memo LIKE 'REFUND:%%'",
        (currency, currency),
    )
    position = cash_position(
        BalanceView(Money(balance["available_minor"], currency), balance["last_refresh_time"]),
        [Outflow(Money(r["amount_minor"], currency), r["submitted_at"]) for r in outflow_rows],
        [Reservation(r["kind"], Money(r["amount_minor"], currency)) for r in reservations],
    )
    # The run's existing cash commitment is replaced by the simulated payout, not charged twice.
    own = sum(
        r["amount_minor"]
        for r in reservations
        if r["run_id"] == run_id and r["kind"] in {"RUN_CASH", "PROPOSAL_CASH"}
    )
    other = position.cash_reserved.minor - own
    before = position.effective_available.minor - other
    after = before - payout
    return {
        "run_id": str(run_id),
        "respect_reserves": respect_reserves,
        "currency": currency,
        "available": True,
        "last_refresh_time": balance["last_refresh_time"].isoformat().replace("+00:00", "Z"),
        "snapshot_available": money(position.available.minor, currency),
        "outflows_after_refresh": money(position.outflows_after_refresh.minor, currency),
        "effective_available": money(position.effective_available.minor, currency),
        "other_cash_reservations": money(other, currency),
        "own_cash_reservation_replaced": money(own, currency),
        "dispute_earmarks_not_subtracted": money(position.earmarked.minor, currency),
        "disputed_shares": money(withheld, currency),
        "payout": money(payout, currency),
        "available_change": money(-payout, currency),
        "unreserved_before": money(before, currency),
        "unreserved_after": money(after, currency),
        "shortfall": money(max(0, -after), currency),
        "pays_disputed_shares": not respect_reserves and withheld > 0,
        "executable": False,
    }


class RunSimulator:
    def __init__(self, database):
        self.database = database

    async def simulate(self, request):
        if (
            not isinstance(request, dict)
            or set(request) - {"run_id", "respect_reserves"}
            or "run_id" not in request
            or type(request.get("respect_reserves", True)) is not bool
        ):
            raise SimulationError("INVALID_SIMULATION_REQUEST")
        try:
            if not isinstance(request["run_id"], str):
                raise TypeError
            run_id = UUID(request["run_id"])
        except (ValueError, TypeError, AttributeError):
            raise SimulationError("INVALID_RUN_IDENTIFIER") from None
        try:
            async with self.database.tx() as conn:
                await conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                return await simulate_run(
                    conn, run_id, respect_reserves=request.get("respect_reserves", True)
                )
        except SimulationError:
            raise
        except Exception:  # noqa: BLE001 - private driver values never leave this boundary.
            raise SimulationError("SIMULATION_UNAVAILABLE") from None
