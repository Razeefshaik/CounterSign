"""What the shield sees: a proposal plus a frozen snapshot of every record its rules read.

The caller (the engine facade before approval, the executor inside its locked transaction) loads
the rows and builds these dataclasses; the shield itself never does I/O. Field names follow the
PLAN 2.2 columns. Anything a rule needs but cannot find here is treated as unknown, and unknown
blocks (fail closed).
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from functools import cached_property
from types import MappingProxyType
from typing import Any

from countersign.fsm.attempt import AttemptState
from countersign.fsm.dispute import Phase
from countersign.fsm.line import LineState
from countersign.fsm.obligation import ObligationState
from countersign.fsm.operation import OperationState
from countersign.ledger.cash import CashPosition
from countersign.money import Money


class Action(StrEnum):
    """Proposal actions (CONTRACTS section 1)."""

    WAIT = "WAIT"
    DRAFT_MESSAGE = "DRAFT_MESSAGE"
    PROVIDE_EVIDENCE = "PROVIDE_EVIDENCE"
    CANCEL_UNCLAIMED_PAYOUT = "CANCEL_UNCLAIMED_PAYOUT"
    RESERVE_EARNINGS = "RESERVE_EARNINGS"
    RELEASE_RESERVE = "RELEASE_RESERVE"
    SCHEDULE_PAYOUT_RUN = "SCHEDULE_PAYOUT_RUN"
    CREATE_REPLACEMENT_PAYOUT = "CREATE_REPLACEMENT_PAYOUT"
    DEFER_SCHEDULED_PAYOUT = "DEFER_SCHEDULED_PAYOUT"
    ACCEPT_CLAIM = "ACCEPT_CLAIM"
    REQUEST_OWNER = "REQUEST_OWNER"


class Tier(StrEnum):
    AUTONOMOUS = "AUTONOMOUS"
    OWNER = "OWNER"


A = Action

# HANDOFF section 6: an action that sends money out or gives money back to a buyer needs the owner.
TIER: Mapping[Action, Tier] = MappingProxyType(
    {a: Tier.AUTONOMOUS for a in Action}
    | {
        A.SCHEDULE_PAYOUT_RUN: Tier.OWNER,
        A.CREATE_REPLACEMENT_PAYOUT: Tier.OWNER,
        A.DEFER_SCHEDULED_PAYOUT: Tier.OWNER,
        A.ACCEPT_CLAIM: Tier.OWNER,
    }
)

# Actions that send a payout item to a maker.
PAYING = frozenset({A.SCHEDULE_PAYOUT_RUN, A.CREATE_REPLACEMENT_PAYOUT})

# Actions the executor turns into a PayPal write, and the operations.kind each one records.
OPERATION_KIND: Mapping[Action, str] = MappingProxyType(
    {
        A.SCHEDULE_PAYOUT_RUN: "PAYOUT_CREATE",
        A.CREATE_REPLACEMENT_PAYOUT: "PAYOUT_CREATE",
        A.CANCEL_UNCLAIMED_PAYOUT: "PAYOUT_CANCEL",
        A.PROVIDE_EVIDENCE: "DISPUTE_EVIDENCE",
        A.ACCEPT_CLAIM: "DISPUTE_ACCEPT_CLAIM",
    }
)
PAYPAL_WRITES = frozenset(OPERATION_KIND)

# Actions that touch no money record at all.
NON_FINANCIAL = frozenset({A.WAIT, A.DRAFT_MESSAGE, A.REQUEST_OWNER})

del A


def _coerce(record: object, name: str, enum: type[StrEnum]) -> None:
    """Accept the stored string and keep the enum, so rules can compare with `is`."""
    object.__setattr__(record, name, enum(getattr(record, name)))


def _aware(value: datetime | None, what: str) -> None:
    if value is not None and value.utcoffset() is None:
        raise ValueError(f"{what} must be timezone-aware.")


@dataclass(frozen=True)
class Proposal:
    """One structured proposal. params are untrusted agent output; payload_hash is "sha256:<hex>"
    of the canonical operation payload the executor would send."""

    id: str
    action: Action
    params: Mapping[str, Any]
    payload_hash: str

    def __post_init__(self):
        object.__setattr__(self, "action", Action(self.action))


@dataclass(frozen=True)
class PayoutItem:
    """One payout item the proposal would send, exactly as the operation payload holds it."""

    line_id: str
    attempt_no: int
    maker_id: str
    amount: Money
    payee_email: str
    sender_batch_id: str
    request_id: str
    sender_item_id: str


@dataclass(frozen=True)
class Targets:
    """Records the action acts on directly, other than the lines its payout items pay."""

    line_ids: tuple[str, ...] = ()
    obligation_ids: tuple[str, ...] = ()
    attempt_ids: tuple[str, ...] = ()
    dispute_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class Obligation:
    id: str
    maker_id: str
    order_id: str | None
    state: ObligationState
    amount: Money
    version: int
    line_id: str | None = None  # the line it is actively allocated to, if any

    def __post_init__(self):
        _coerce(self, "state", ObligationState)


@dataclass(frozen=True)
class Line:
    id: str
    run_id: str
    maker_id: str
    state: LineState
    amount: Money
    version: int

    def __post_init__(self):
        _coerce(self, "state", LineState)


@dataclass(frozen=True)
class Attempt:
    id: str
    line_id: str
    attempt_no: int
    state: AttemptState
    operation_id: str
    sender_batch_id: str
    request_id: str
    sender_item_id: str
    payee_email: str
    amount: Money
    prepared_at: datetime
    last_checked_at: datetime | None = None  # time of the last PayPal GET of the item
    version: int = 1

    def __post_init__(self):
        _coerce(self, "state", AttemptState)
        _aware(self.prepared_at, "prepared_at")
        _aware(self.last_checked_at, "last_checked_at")


@dataclass(frozen=True)
class Operation:
    id: str
    kind: str
    state: OperationState
    payload_hash: str
    proposal_id: str | None = None
    target_id: str | None = None

    def __post_init__(self):
        _coerce(self, "state", OperationState)


@dataclass(frozen=True)
class Maker:
    id: str
    payout_email: str | None
    payout_email_verified_at: datetime | None
    version: int

    def __post_init__(self):
        _aware(self.payout_email_verified_at, "payout_email_verified_at")


@dataclass(frozen=True)
class Dispute:
    id: str
    order_id: str | None
    phase: Phase
    version: int

    def __post_init__(self):
        _coerce(self, "phase", Phase)


@dataclass(frozen=True)
class ReservationRecord:
    """A reservations row; cash.Reservation is the narrower view the funding formula takes."""

    id: str
    kind: str
    state: str
    amount: Money
    obligation_id: str | None = None
    dispute_id: str | None = None
    proposal_id: str | None = None
    run_id: str | None = None


@dataclass(frozen=True)
class Approval:
    id: str
    proposal_id: str
    payload_hash: str
    record_versions: Mapping[str, int]
    expires_at: datetime
    used_at: datetime | None = None

    def __post_init__(self):
        _aware(self.expires_at, "expires_at")
        _aware(self.used_at, "used_at")


@dataclass(frozen=True)
class Scope:
    """Every record id a proposal touches; UNKNOWN_SCOPED and APPROVAL_BINDING work on this."""

    lines: frozenset[str]
    obligations: frozenset[str]
    attempts: frozenset[str]
    disputes: frozenset[str]
    makers: frozenset[str]


@dataclass(frozen=True)
class Snapshot:
    """Everything the rules read, loaded by the caller. It may hold unrelated records too."""

    now: datetime
    cash: CashPosition
    at_execution: bool = False
    payouts: tuple[PayoutItem, ...] = ()
    targets: Targets = field(default_factory=Targets)
    obligations: tuple[Obligation, ...] = ()
    lines: tuple[Line, ...] = ()
    attempts: tuple[Attempt, ...] = ()
    operations: tuple[Operation, ...] = ()
    makers: tuple[Maker, ...] = ()
    disputes: tuple[Dispute, ...] = ()
    reservations: tuple[ReservationRecord, ...] = ()
    operation: Operation | None = None  # this proposal's write-ahead record (at execution)
    approval: Approval | None = None

    def __post_init__(self):
        _aware(self.now, "now")

    @cached_property
    def obligation_by_id(self) -> Mapping[str, Obligation]:
        return {o.id: o for o in self.obligations}

    @cached_property
    def line_by_id(self) -> Mapping[str, Line]:
        return {line.id: line for line in self.lines}

    @cached_property
    def attempt_by_id(self) -> Mapping[str, Attempt]:
        return {a.id: a for a in self.attempts}

    @cached_property
    def maker_by_id(self) -> Mapping[str, Maker]:
        return {m.id: m for m in self.makers}

    @cached_property
    def dispute_by_id(self) -> Mapping[str, Dispute]:
        return {d.id: d for d in self.disputes}

    def line_obligations(self, line_id: str) -> list[Obligation]:
        return [o for o in self.obligations if o.line_id == line_id]

    def line_attempts(self, line_id: str) -> list[Attempt]:
        return sorted(
            (a for a in self.attempts if a.line_id == line_id), key=lambda a: a.attempt_no
        )

    def order_disputes(self, order_id: str | None) -> list[Dispute]:
        return [d for d in self.disputes if order_id is not None and d.order_id == order_id]

    def replay_of(self, item: PayoutItem) -> Attempt | None:
        """The UNKNOWN attempt this item replays byte for byte (same keys, payee and amount)."""
        for a in self.line_attempts(item.line_id):
            if (
                a.attempt_no == item.attempt_no
                and a.state is AttemptState.UNKNOWN
                and (a.sender_batch_id, a.request_id, a.sender_item_id)
                == (item.sender_batch_id, item.request_id, item.sender_item_id)
                and a.payee_email == item.payee_email
                and a.amount == item.amount
            ):
                return a
        return None

    @cached_property
    def scope(self) -> Scope:
        t = self.targets
        lines = {i.line_id for i in self.payouts} | set(t.line_ids)
        lines |= {self.attempt_by_id[a].line_id for a in t.attempt_ids if a in self.attempt_by_id}
        lines |= {
            o.line_id
            for o in map(self.obligation_by_id.get, t.obligation_ids)
            if o is not None and o.line_id is not None
        }
        obligations = set(t.obligation_ids) | {o.id for o in self.obligations if o.line_id in lines}
        orders = {
            self.obligation_by_id[o].order_id for o in obligations if o in self.obligation_by_id
        }
        disputes = set(t.dispute_ids) | {
            d.id for d in self.disputes if d.order_id in orders - {None}
        }
        attempts = set(t.attempt_ids) | {a.id for a in self.attempts if a.line_id in lines}
        makers = {i.maker_id for i in self.payouts}
        makers |= {self.line_by_id[x].maker_id for x in lines if x in self.line_by_id}
        return Scope(*map(frozenset, (lines, obligations, attempts, disputes, makers)))

    def missing(self) -> list[str]:
        """Touched ids whose record was not loaded: their state is unknown."""
        s = self.scope
        pairs = [
            ("line", s.lines, self.line_by_id),
            ("obligation", s.obligations, self.obligation_by_id),
            ("attempt", s.attempts, self.attempt_by_id),
            ("dispute", s.disputes, self.dispute_by_id),
            ("maker", s.makers, self.maker_by_id),
        ]
        return sorted(f"{kind}:{i}" for kind, ids, known in pairs for i in ids if i not in known)

    def record_versions(self) -> dict[str, int]:
        """Versions an approval binds: the touched makers, lines and obligations, plus the
        attempts and disputes the action targets directly. Keys are "<kind>:<id>" (PLAN 2.2).

        Attempts and disputes reached only through a line or an order are left out: every
        PayPal sync bumps their version, and the rules re-check their state at execution.
        """
        s, t = self.scope, self.targets
        versions = {
            f"maker:{i}": self.maker_by_id[i].version for i in s.makers if i in self.maker_by_id
        }
        versions |= {
            f"line:{i}": self.line_by_id[i].version for i in s.lines if i in self.line_by_id
        }
        versions |= {
            f"obligation:{i}": self.obligation_by_id[i].version
            for i in s.obligations
            if i in self.obligation_by_id
        }
        versions |= {
            f"attempt:{i}": self.attempt_by_id[i].version
            for i in t.attempt_ids
            if i in self.attempt_by_id
        }
        versions |= {
            f"dispute:{i}": self.dispute_by_id[i].version
            for i in t.dispute_ids
            if i in self.dispute_by_id
        }
        return versions
