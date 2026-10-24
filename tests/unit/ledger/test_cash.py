from datetime import UTC, datetime, timedelta

import pytest

from countersign.ledger.cash import BalanceView, Outflow, Reservation, cash_position
from countersign.money import CurrencyMismatch, Money

REFRESH = datetime(2026, 11, 5, 9, 0, tzinfo=UTC)


def usd(dollars: int) -> Money:
    return Money(dollars * 100, "USD")


def test_demo_week_thursday_numbers_reproduce_exactly():
    # HANDOFF section 10: $1,340 available; A and B dispute shares earmarked ($360, $54);
    # the owner approves one run of B $486 + C $300 + C $300 = $1,086; $254 stays free.
    position = cash_position(
        BalanceView(available=usd(1340), last_refresh_time=REFRESH),
        outflows=[],
        reservations=[
            Reservation("DISPUTE_EARMARK", usd(360)),
            Reservation("DISPUTE_EARMARK", usd(54)),
            Reservation("RUN_CASH", usd(486) + usd(300) + usd(300)),
        ],
    )
    assert position.cash_reserved == usd(1086)
    assert position.earmarked == usd(414)
    assert position.unreserved == usd(254)


def test_dispute_earmarks_never_reduce_unreserved():
    balance = BalanceView(available=usd(1000), last_refresh_time=REFRESH)
    without = cash_position(balance, [], [])
    with_earmark = cash_position(balance, [], [Reservation("DISPUTE_EARMARK", usd(900))])
    assert with_earmark.unreserved == without.unreserved == usd(1000)


def test_only_outflows_after_the_refresh_are_subtracted():
    position = cash_position(
        BalanceView(available=usd(1000), last_refresh_time=REFRESH),
        outflows=[
            Outflow(usd(100), REFRESH - timedelta(minutes=5)),  # already in the snapshot
            Outflow(usd(40), REFRESH),  # at the refresh instant: assumed reflected
            Outflow(usd(25), REFRESH + timedelta(minutes=1)),  # not yet reflected
        ],
        reservations=[],
    )
    assert position.outflows_after_refresh == usd(25)
    assert position.effective_available == position.unreserved == usd(975)


def test_unknown_refresh_time_counts_every_outflow():
    position = cash_position(
        BalanceView(available=usd(500), last_refresh_time=None),
        outflows=[Outflow(usd(100), REFRESH), Outflow(usd(50), REFRESH)],
        reservations=[Reservation("PROPOSAL_CASH", usd(60))],
    )
    assert position.unreserved == usd(290)


def test_unreserved_can_go_negative_so_funding_can_block():
    position = cash_position(
        BalanceView(available=usd(100), last_refresh_time=REFRESH),
        outflows=[],
        reservations=[Reservation("RUN_CASH", usd(150))],
    )
    assert position.unreserved == usd(-50)


def test_currencies_never_mix():
    with pytest.raises(CurrencyMismatch):
        cash_position(
            BalanceView(available=usd(100), last_refresh_time=REFRESH),
            outflows=[Outflow(Money(100, "EUR"), REFRESH + timedelta(minutes=1))],
            reservations=[],
        )


@pytest.mark.parametrize(("kind", "amount"), [("HOLD", usd(1)), ("RUN_CASH", usd(0))])
def test_reservations_reject_unknown_kinds_and_non_positive_amounts(kind, amount):
    with pytest.raises(ValueError):
        Reservation(kind, amount)
