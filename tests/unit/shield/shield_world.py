"""A small demo-week world for shield tests (HANDOFF section 10 numbers).

Friday: the run pays Maker B $486 (line LB, attempt 1) and resends Maker C's returned $300
(last week's line LC, attempt 2). Cash: $1,340 available, $414 of dispute earmarks that never
touch cash, and the run's $786 RUN_CASH reservation.
"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

from countersign.fsm.attempt import AttemptState
from countersign.fsm.dispute import Phase
from countersign.fsm.line import LineState
from countersign.fsm.obligation import ObligationState
from countersign.fsm.operation import OperationState
from countersign.ledger.cash import BalanceView, Reservation, cash_position
from countersign.money import Money
from countersign.shield.rules.idempotency import derive_keys
from countersign.shield.snapshot import (
    Action,
    Approval,
    Attempt,
    Dispute,
    Line,
    Maker,
    Obligation,
    Operation,
    PayoutItem,
    Proposal,
    ReservationRecord,
    Snapshot,
    Targets,
)

NOW = datetime(2026, 11, 6, 9, 0, tzinfo=UTC)
HASH = "sha256:9c1e00000000000000000000000000000000000000000000000000000000beef"


def v7(n: int) -> str:
    """A fixed, valid UUIDv7 string; n keeps ids distinct and readable."""
    return str(UUID(int=(0x0192F1C47A10 << 80) | (7 << 76) | (2 << 62) | n))


def usd(dollars: int) -> Money:
    return Money(dollars * 100, "USD")


PROPOSAL = v7(0x401)
RUN, OLD_RUN = v7(0x601), v7(0x600)
MAKER_A, MAKER_B, MAKER_C = v7(0x301), v7(0x302), v7(0x303)
LINE_B, LINE_C = v7(0x701), v7(0x699)
OBL_A, OBL_B, OBL_C = v7(0x802), v7(0x801), v7(0x803)
ORDER_A, ORDER_B, ORDER_C = v7(0x201), v7(0x202), v7(0x203)
DISPUTE_A = v7(0x101)
ATT_C1, OP_C1, OP_RUN = v7(0x901), v7(0x501), v7(0x502)
EMAIL_B, EMAIL_C, EMAIL_C_TYPO = "maker-b@example.com", "maker-c@example.com", "maker-c@exmaple.com"


def item(line_id: str, attempt_no: int, maker_id: str, amount: Money, email: str) -> PayoutItem:
    keys = derive_keys(line_id, attempt_no)
    return PayoutItem(
        line_id=line_id,
        attempt_no=attempt_no,
        maker_id=maker_id,
        amount=amount,
        payee_email=email,
        sender_batch_id=keys.sender_batch_id,
        request_id=keys.request_id,
        sender_item_id=keys.sender_item_id,
    )


def attempt(
    line_id: str,
    attempt_no: int,
    state: AttemptState,
    *,
    id: str = ATT_C1,
    email: str = EMAIL_C_TYPO,
    amount: Money | None = None,
    prepared_at: datetime | None = None,
    checked: bool = True,
    operation_id: str = OP_C1,
) -> Attempt:
    keys = derive_keys(line_id, attempt_no)
    return Attempt(
        id=id,
        line_id=line_id,
        attempt_no=attempt_no,
        state=state,
        operation_id=operation_id,
        sender_batch_id=keys.sender_batch_id,
        request_id=keys.request_id,
        sender_item_id=keys.sender_item_id,
        payee_email=email,
        amount=amount or usd(300),
        prepared_at=prepared_at or NOW - timedelta(days=7),
        last_checked_at=NOW - timedelta(hours=1) if checked else None,
    )


def cash(available: int = 1340, run_cash: int = 786):
    reservations = [
        Reservation("DISPUTE_EARMARK", usd(360)),
        Reservation("DISPUTE_EARMARK", usd(54)),
    ]
    if run_cash:
        reservations.append(Reservation("RUN_CASH", usd(run_cash)))
    return cash_position(BalanceView(usd(available), NOW - timedelta(hours=3)), [], reservations)


def run_cash_record(amount: int = 786, **kw) -> ReservationRecord:
    base = {
        "id": v7(0xB10),
        "kind": "RUN_CASH",
        "state": "ACTIVE",
        "amount": usd(amount),
        "run_id": RUN,
    }
    return ReservationRecord(**(base | kw))


MAKERS = (
    Maker(MAKER_A, "maker-a@example.com", NOW - timedelta(days=30), 1),
    Maker(MAKER_B, EMAIL_B, NOW - timedelta(days=30), 2),
    Maker(MAKER_C, EMAIL_C, NOW - timedelta(days=2), 3),
)
LINES = (
    Line(LINE_B, RUN, MAKER_B, LineState.APPROVED, usd(486), 1),
    Line(LINE_C, OLD_RUN, MAKER_C, LineState.UNPAID, usd(300), 4),
)
OBLIGATIONS = (
    Obligation(OBL_A, MAKER_A, ORDER_A, ObligationState.RESERVED, usd(360), 2),
    Obligation(OBL_B, MAKER_B, ORDER_B, ObligationState.SCHEDULED, usd(486), 3, line_id=LINE_B),
    Obligation(OBL_C, MAKER_C, ORDER_C, ObligationState.SCHEDULED, usd(300), 5, line_id=LINE_C),
)
DISPUTES = (Dispute(DISPUTE_A, ORDER_A, Phase.ACTIVE, 1),)
EARMARK_A = ReservationRecord(
    v7(0xB01), "DISPUTE_EARMARK", "ACTIVE", usd(360), obligation_id=OBL_A, dispute_id=DISPUTE_A
)


class World:
    """Builders for the Friday run; tests change one thing with dataclasses.replace."""

    now = NOW

    def proposal(self, action: Action = Action.SCHEDULE_PAYOUT_RUN, **kw) -> Proposal:
        params = kw.pop("params", {"run_id": RUN})
        return Proposal(id=kw.pop("id", PROPOSAL), action=action, params=params, payload_hash=HASH)

    def pay_b(self) -> PayoutItem:
        return item(LINE_B, 1, MAKER_B, usd(486), EMAIL_B)

    def resend_c(self) -> PayoutItem:
        return item(LINE_C, 2, MAKER_C, usd(300), EMAIL_C)

    def run(self, **kw) -> Snapshot:
        """Before approval: the Friday run paying B and resending C."""
        base = {
            "now": NOW,
            "cash": cash(),
            "payouts": (self.pay_b(), self.resend_c()),
            "obligations": OBLIGATIONS,
            "lines": LINES,
            "attempts": (attempt(LINE_C, 1, AttemptState.RETURNED),),
            "makers": MAKERS,
            "disputes": DISPUTES,
            "reservations": (EARMARK_A, run_cash_record()),
        }
        return Snapshot(**(base | kw))

    def operation(self, **kw) -> Operation:
        base = {
            "id": OP_RUN,
            "kind": "PAYOUT_CREATE",
            "state": OperationState.PENDING,
            "payload_hash": HASH,
            "proposal_id": PROPOSAL,
            "target_id": RUN,
        }
        return Operation(**(base | kw))

    def approval(self, snap: Snapshot, **kw) -> Approval:
        base = {
            "id": v7(0xA01),
            "proposal_id": PROPOSAL,
            "payload_hash": HASH,
            "record_versions": snap.record_versions(),
            "expires_at": NOW + timedelta(hours=8),
            "used_at": None,
        }
        return Approval(**(base | kw))

    def executing(self, snap: Snapshot | None = None, **kw) -> Snapshot:
        """The same run inside the executor's transaction, approved and written ahead."""
        snap = snap or self.run()
        snap = replace(snap, at_execution=True, operation=self.operation())
        return replace(snap, approval=self.approval(snap), **kw)

    def cancel_c(self, state: AttemptState = AttemptState.UNCLAIMED, **kw) -> Snapshot:
        """Thursday: cancel Maker C's unclaimed payout (autonomous)."""
        line_c = replace(LINES[1], state=LineState.PAYING)
        obl_c = replace(OBLIGATIONS[2], state=ObligationState.IN_FLIGHT)
        base = {
            "now": NOW,
            "cash": cash(),
            "targets": Targets(attempt_ids=(ATT_C1,)),
            "obligations": (obl_c,),
            "lines": (line_c,),
            "attempts": (attempt(LINE_C, 1, state),),
            "makers": MAKERS,
        }
        return Snapshot(**(base | kw))
