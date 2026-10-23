from collections import Counter
from uuid import UUID

import pytest

from countersign.ledger import journal
from countersign.ledger.journal import (
    AVAILABLE,
    BUYER_REFUNDS,
    IN_TRANSIT,
    UNCLAIMED,
    WITHHELD,
    Event,
    JournalDraft,
    Posting,
    UnbalancedJournal,
    maker_payable,
)
from countersign.ledger.reservations import ReservationError, dispute_earmark_amount
from countersign.money import Money, MoneyError

MAKER = UUID("0192f0a0-0000-7000-8000-00000000000a")


def usd(dollars: int) -> Money:
    return Money(dollars * 100, "USD")


def every_draft(amount: Money) -> list[JournalDraft]:
    return [
        journal.payout_submitted(amount),
        journal.payout_unclaimed(amount),
        journal.payout_success(amount, MAKER),
        journal.payout_returned(amount, was_unclaimed=True),
        journal.payout_returned(amount, was_unclaimed=False),
        journal.payout_failed(amount),
        journal.refund(amount, source=AVAILABLE),
        journal.refund(amount, source=WITHHELD),
    ]


def net(drafts: list[JournalDraft]) -> Counter:
    totals: Counter = Counter()
    for draft in drafts:
        for posting in draft.postings:
            totals[posting.account] += posting.amount.minor
    return Counter({account: minor for account, minor in totals.items() if minor})


@pytest.mark.parametrize("minor", [1, 99, 30000, 48600, 10**12])
@pytest.mark.parametrize("currency", ["USD", "JPY"])
def test_every_builder_balances_per_currency(minor, currency):
    for draft in every_draft(Money(minor, currency)):
        assert sum(p.amount.minor for p in draft.postings) == 0
        assert {p.amount.currency for p in draft.postings} == {currency}
        assert all(p.amount.minor != 0 for p in draft.postings)


def test_builders_move_money_between_the_plan_accounts():
    amount = usd(300)
    assert journal.payout_submitted(amount).postings == (
        Posting(IN_TRANSIT, amount),
        Posting(AVAILABLE, -amount),
    )
    assert journal.payout_unclaimed(amount).postings == (
        Posting(UNCLAIMED, amount),
        Posting(IN_TRANSIT, -amount),
    )
    assert journal.payout_success(amount, MAKER).postings == (
        Posting(f"maker_payable:{MAKER}", amount),
        Posting(IN_TRANSIT, -amount),
    )
    assert journal.payout_returned(amount, was_unclaimed=True).postings == (
        Posting(AVAILABLE, amount),
        Posting(UNCLAIMED, -amount),
    )
    assert journal.payout_returned(amount, was_unclaimed=False).postings == (
        Posting(AVAILABLE, amount),
        Posting(IN_TRANSIT, -amount),
    )
    assert journal.refund(amount, source=WITHHELD).postings == (
        Posting(BUYER_REFUNDS, amount),
        Posting(WITHHELD, -amount),
    )


def test_paths_through_transit_net_out():
    amount = usd(300)
    settled = [journal.payout_submitted(amount), journal.payout_success(amount, MAKER)]
    assert net(settled) == Counter({maker_payable(MAKER): 30000, AVAILABLE: -30000})
    # Maker C's typo: submitted, unclaimed, cancelled and RETURNED leaves nothing behind.
    returned = [
        journal.payout_submitted(amount),
        journal.payout_unclaimed(amount),
        journal.payout_returned(amount, was_unclaimed=True),
    ]
    assert net(returned) == Counter()
    failed = [journal.payout_submitted(amount), journal.payout_failed(amount)]
    assert net(failed) == Counter()


def test_events_and_memos_are_fixed():
    drafts = every_draft(usd(1))
    assert [d.event for d in drafts] == [
        Event.PAYOUT_SUBMITTED,
        Event.PAYOUT_UNCLAIMED,
        Event.PAYOUT_SUCCESS,
        Event.PAYOUT_RETURNED,
        Event.PAYOUT_RETURNED,
        Event.PAYOUT_FAILED,
        Event.REFUND,
        Event.REFUND,
    ]
    assert all(d.stored_memo.startswith(f"{d.event}: ") for d in drafts)


@pytest.mark.parametrize("bad", [Money(0, "USD"), Money(-1, "USD"), 300, None])
def test_builders_reject_non_positive_or_non_money(bad):
    with pytest.raises(UnbalancedJournal):
        journal.payout_submitted(bad)
    with pytest.raises(UnbalancedJournal):
        journal.payout_success(bad, MAKER)


def test_refund_source_must_be_named_and_known():
    with pytest.raises(UnbalancedJournal):
        journal.refund(usd(60), source=IN_TRANSIT)
    with pytest.raises(TypeError):
        journal.refund(usd(60))  # type: ignore[call-arg]


def test_hand_made_unbalanced_drafts_are_rejected_before_sql():
    with pytest.raises(UnbalancedJournal, match="balance"):
        JournalDraft(
            Event.REFUND, "x", (Posting(BUYER_REFUNDS, usd(60)), Posting(AVAILABLE, usd(-59)))
        )
    with pytest.raises(UnbalancedJournal, match="balance"):
        JournalDraft(
            Event.REFUND,
            "mixed currencies",
            (
                Posting(BUYER_REFUNDS, usd(60)),
                Posting(AVAILABLE, Money(-6000, "EUR")),
            ),
        )
    with pytest.raises(UnbalancedJournal, match="two postings"):
        JournalDraft(Event.REFUND, "x", (Posting(BUYER_REFUNDS, Money(0, "USD")),))
    with pytest.raises(UnbalancedJournal, match="non-zero"):
        JournalDraft(
            Event.REFUND,
            "x",
            (Posting(BUYER_REFUNDS, Money(0, "USD")), Posting(AVAILABLE, Money(0, "USD"))),
        )


@pytest.mark.parametrize(
    "account",
    ["paypal:everything", "maker_payable:", "maker_payable:not-a-uuid", "AVAILABLE", ""],
)
def test_unknown_accounts_are_rejected(account):
    with pytest.raises(UnbalancedJournal, match="account"):
        JournalDraft(Event.REFUND, "x", (Posting(account, usd(1)), Posting(AVAILABLE, usd(-1))))


def test_unknown_event_is_rejected():
    with pytest.raises(ValueError):
        JournalDraft("PAYOUT_MAYBE", "x", (Posting(AVAILABLE, usd(1)), Posting(WITHHELD, usd(-1))))


def test_demo_earmarks_use_the_maker_share():
    assert dispute_earmark_amount(usd(400), 90) == usd(360)
    assert dispute_earmark_amount(usd(60), 90) == usd(54)
    assert dispute_earmark_amount(Money(33333, "USD"), 90) == usd(300)


@pytest.mark.parametrize("bad", [Money(0, "USD"), Money(-100, "USD"), 400])
def test_earmark_needs_a_positive_order_amount(bad):
    with pytest.raises(ReservationError):
        dispute_earmark_amount(bad, 90)


def test_earmark_share_must_be_a_percent():
    with pytest.raises(MoneyError):
        dispute_earmark_amount(usd(400), 101)
