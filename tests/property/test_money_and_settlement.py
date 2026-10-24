"""Generated event traces exercise the real FSM and conservative funding formula."""

from datetime import UTC, datetime, timedelta

from hypothesis import example, given, settings
from hypothesis import strategies as st

from countersign.fsm import IllegalTransition
from countersign.fsm.obligation import OBLIGATION
from countersign.fsm.obligation import ObligationEvent as E
from countersign.fsm.obligation import ObligationState as S
from countersign.ledger.cash import BalanceView, Outflow, Reservation, cash_position
from countersign.money import Money


@settings(max_examples=200)
@given(events=st.lists(st.sampled_from(tuple(E)), min_size=1, max_size=120))
@example(
    events=[
        E.LINE_APPROVED,
        E.ATTEMPT_PREPARED,
        E.ATTEMPT_PAID,
        E.PAYOUT_CLAWED_BACK,
        E.LINE_APPROVED,
        E.ATTEMPT_PREPARED,
        E.ATTEMPT_PAID,
    ]
)
def test_obligation_settles_once_between_confirmed_clawbacks(events):
    state, settlements, total = S.OPEN, 0, 0
    for event in events:
        before = state
        try:
            state = OBLIGATION.next(state, event)
        except IllegalTransition:
            assert state is before
            continue
        if state is S.SETTLED:
            settlements += 1
            total += 1
            assert before is S.IN_FLIGHT and event is E.ATTEMPT_PAID
            assert settlements == 1
        if event is E.PAYOUT_CLAWED_BACK:
            assert before is S.SETTLED and state is S.OPEN
            assert settlements == 1
            settlements = 0
        assert settlements <= 1
    assert total <= 1 + events.count(E.PAYOUT_CLAWED_BACK)


@settings(max_examples=200)
@given(
    currency=st.sampled_from(("USD", "EUR", "JPY")),
    available=st.integers(0, 10**12),
    marks=st.lists(st.integers(1, 10**10), max_size=30),
    commitments=st.lists(
        st.tuples(st.sampled_from(("PROPOSAL_CASH", "RUN_CASH")), st.integers(1, 10**10)),
        max_size=30,
    ),
    flows=st.lists(st.tuples(st.integers(1, 10**10), st.integers(-100, 100)), max_size=30),
    missing_refresh=st.booleans(),
)
def test_earmarks_never_change_cash_but_cash_reservations_do(
    currency, available, marks, commitments, flows, missing_refresh
):
    now = datetime(2026, 11, 6, tzinfo=UTC)
    balance = BalanceView(Money(available, currency), None if missing_refresh else now)
    outflows = [
        Outflow(Money(minor, currency), now + timedelta(seconds=delta)) for minor, delta in flows
    ]
    cash = [Reservation(kind, Money(minor, currency)) for kind, minor in commitments]
    earmarks = [Reservation("DISPUTE_EARMARK", Money(minor, currency)) for minor in marks]
    baseline = cash_position(balance, outflows, [])
    reserved = cash_position(balance, outflows, cash)
    with_marks = cash_position(balance, outflows, cash + earmarks)
    assert baseline.outflows_after_refresh.minor == sum(
        minor for minor, delta in flows if missing_refresh or delta > 0
    )
    assert reserved.unreserved.minor == baseline.unreserved.minor - sum(
        minor for _, minor in commitments
    )
    assert with_marks.unreserved == reserved.unreserved
    assert with_marks.cash_reserved == reserved.cash_reserved
    assert with_marks.effective_available == reserved.effective_available
    assert with_marks.earmarked.minor == sum(marks)
