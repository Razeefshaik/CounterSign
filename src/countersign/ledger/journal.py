"""Double-entry journals for payout and refund money moves (PLAN 2.2 accounts, C16).

Sign convention: a positive posting is a debit, a negative one a credit. Asset accounts
(paypal:*) grow with debits; maker_payable:<id> is a liability, so paying a maker debits it.
Every journal sums to zero per currency. Builders check that before any SQL, and the
database's deferred trigger checks it again at commit.

Idempotency: ledger_journals has no event column and no unique key, so the event is the
memo prefix ("PAYOUT_SUBMITTED: ...") and post_journal serialises writers of one
(operation_id, event) with a transaction-scoped advisory lock before looking for an
existing journal. That only holds under READ COMMITTED (a later statement sees the
winner's commit), so other isolation levels are refused.
"""

import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from psycopg.pq import TransactionStatus

from countersign.db import Repositories
from countersign.db.pool import Connection
from countersign.money import Money

AVAILABLE = "paypal:available"
WITHHELD = "paypal:withheld"
IN_TRANSIT = "paypal:payouts_in_transit"
UNCLAIMED = "paypal:unclaimed"
PLATFORM_FEES = "platform:fees"
PAYPAL_FEES = "paypal:fees"
BUYER_REFUNDS = "buyer:refunds"
FIXED_ACCOUNTS = frozenset(
    {AVAILABLE, WITHHELD, IN_TRANSIT, UNCLAIMED, PLATFORM_FEES, PAYPAL_FEES, BUYER_REFUNDS}
)
_MAKER_PAYABLE = re.compile(
    r"maker_payable:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)


class Event(StrEnum):
    PAYOUT_SUBMITTED = "PAYOUT_SUBMITTED"
    PAYOUT_UNCLAIMED = "PAYOUT_UNCLAIMED"
    PAYOUT_SUCCESS = "PAYOUT_SUCCESS"
    PAYOUT_RETURNED = "PAYOUT_RETURNED"
    PAYOUT_FAILED = "PAYOUT_FAILED"
    REFUND = "REFUND"


class UnbalancedJournal(ValueError):
    """Postings do not sum to zero per currency, or are otherwise malformed."""


class JournalConflict(RuntimeError):
    """A journal for this (operation, event) exists with different postings."""


class LedgerTransactionRequired(RuntimeError):
    """Journals are written only inside the caller's READ COMMITTED transaction."""


def maker_payable(maker_id: UUID) -> str:
    return f"maker_payable:{UUID(str(maker_id))}"


def _valid_account(account: str) -> bool:
    return account in FIXED_ACCOUNTS or bool(_MAKER_PAYABLE.fullmatch(account))


@dataclass(frozen=True)
class Posting:
    account: str
    amount: Money


@dataclass(frozen=True)
class JournalDraft:
    """A balanced set of postings for one event, not yet written."""

    event: Event
    memo: str
    postings: tuple[Posting, ...]

    def __post_init__(self):
        Event(self.event)
        if len(self.postings) < 2:
            raise UnbalancedJournal("A journal needs at least two postings.")
        totals: dict[str, int] = {}
        for posting in self.postings:
            if not _valid_account(posting.account):
                raise UnbalancedJournal(f"Unknown ledger account {posting.account!r}.")
            if not isinstance(posting.amount, Money) or posting.amount.minor == 0:
                raise UnbalancedJournal("Postings are non-zero Money amounts.")
            currency = posting.amount.currency
            totals[currency] = totals.get(currency, 0) + posting.amount.minor
        if any(totals.values()):
            raise UnbalancedJournal(f"Journal does not balance: {totals}.")

    @property
    def stored_memo(self) -> str:
        return f"{self.event}: {self.memo}"


def _positive(amount: Money) -> Money:
    if not isinstance(amount, Money) or amount.minor <= 0:
        raise UnbalancedJournal("Journal amounts must be positive Money.")
    return amount


def _move(event: Event, memo: str, amount: Money, debit: str, credit: str) -> JournalDraft:
    amount = _positive(amount)
    return JournalDraft(event, memo, (Posting(debit, amount), Posting(credit, -amount)))


def payout_submitted(amount: Money) -> JournalDraft:
    """PayPal accepted the payout: the money leaves available and is in transit."""
    return _move(Event.PAYOUT_SUBMITTED, "payout submitted", amount, IN_TRANSIT, AVAILABLE)


def payout_unclaimed(amount: Money) -> JournalDraft:
    """The item is UNCLAIMED: still out of available, now waiting on the recipient.

    Assumes unclaimed money is not in PayPal's available balance [pending D4].
    """
    return _move(Event.PAYOUT_UNCLAIMED, "payout unclaimed", amount, UNCLAIMED, IN_TRANSIT)


def payout_success(amount: Money, maker_id: UUID) -> JournalDraft:
    """GET-confirmed SUCCESS: the maker is paid, so what we owe them drops."""
    account = maker_payable(maker_id)
    return _move(Event.PAYOUT_SUCCESS, "payout settled", amount, account, IN_TRANSIT)


def payout_returned(amount: Money, *, was_unclaimed: bool) -> JournalDraft:
    """GET-confirmed RETURNED (cancelled or never claimed): the money is available again."""
    source = UNCLAIMED if was_unclaimed else IN_TRANSIT
    return _move(Event.PAYOUT_RETURNED, "payout returned", amount, AVAILABLE, source)


def payout_failed(amount: Money) -> JournalDraft:
    """GET-confirmed FAILED after submission: the money never left."""
    return _move(Event.PAYOUT_FAILED, "payout failed", amount, AVAILABLE, IN_TRANSIT)


def refund(amount: Money, *, source: str) -> JournalDraft:
    """A buyer refund, paid from withheld or available money.

    Which one PayPal uses for a held dispute is [pending D10] (F17), so the caller must say.
    """
    if source not in (AVAILABLE, WITHHELD):
        raise UnbalancedJournal("A refund is paid from paypal:available or paypal:withheld.")
    return _move(Event.REFUND, "buyer refund", amount, BUYER_REFUNDS, source)


async def serialize_on(conn: Connection, key: str) -> None:
    """Hold a transaction-scoped advisory lock on key; READ COMMITTED transactions only."""
    cursor = await conn.execute("SELECT current_setting('transaction_isolation') AS level")
    level = (await cursor.fetchone())["level"]
    if conn.info.transaction_status != TransactionStatus.INTRANS:
        raise LedgerTransactionRequired("Run this inside the caller's transaction.")
    if level != "read committed":
        raise LedgerTransactionRequired(f"Needs READ COMMITTED, not {level}.")
    await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (key,))


def _shape(rows) -> Counter:
    return Counter((r["account"], r["amount_minor"], r["currency"].strip()) for r in rows)


async def post_journal(
    conn: Connection,
    draft: JournalDraft,
    *,
    operation_id: UUID,
    occurred_at: datetime,
    paypal_effective_at: datetime | None = None,
) -> dict:
    """Insert a journal and its postings once per (operation_id, event); return its row.

    A repeat with the same postings returns the existing journal; different postings
    raise JournalConflict. Nothing commits here: the caller's transaction does.
    """
    if not isinstance(draft, JournalDraft):
        raise TypeError("post_journal takes a JournalDraft from a builder.")
    if operation_id is None:
        raise ValueError("Every journal belongs to an operation.")
    await serialize_on(conn, f"countersign:journal:{operation_id}:{draft.event}")
    wanted = Counter((p.account, p.amount.minor, p.amount.currency) for p in draft.postings)
    cursor = await conn.execute(
        "SELECT * FROM ledger_journals WHERE operation_id = %s AND starts_with(memo, %s) "
        "ORDER BY id",
        (operation_id, f"{draft.event}:"),
    )
    existing = await cursor.fetchall()
    repos = Repositories(conn)
    if existing:
        if len(existing) > 1:
            raise JournalConflict(f"{len(existing)} journals already exist for this event.")
        journal = existing[0]
        postings = await repos.ledger_postings.find({"journal_id": journal["id"]})
        if _shape(postings) != wanted:
            raise JournalConflict("This event was already journaled with different postings.")
        return journal
    journal = await repos.ledger_journals.insert(
        operation_id=operation_id,
        occurred_at=occurred_at,
        paypal_effective_at=paypal_effective_at,
        memo=draft.stored_memo,
    )
    for posting in draft.postings:
        await repos.ledger_postings.insert(
            journal_id=journal["id"],
            account=posting.account,
            amount_minor=posting.amount.minor,
            currency=posting.amount.currency,
        )
    return journal
