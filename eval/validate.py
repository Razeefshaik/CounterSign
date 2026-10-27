"""Validate CONTRACTS v1.0.3 scenario files, including cross references.

Run: python -m eval.validate [paths ...]
Regenerate schema: python -m eval.validate --write-schema
No credentials, network calls, or core imports.
The six M08 scenarios are labelled synthesised. demo_week follows HANDOFF's payout
totals and begins with the prior recipient already owner verified; it does not
simulate email correction or next week's settlement. Core share rounding is
not inferred by this validator. Actual evaluation belongs to M11/C20.
M14: "recorded" provenance is refused when a scenario relies on synthesised
behaviour (F13): a synthesised seeded item, a state the sandbox never produced,
an injected transport or webhook fault, or a synthesised /_mock control.
"""

import argparse
import json
import re
from pathlib import Path
from typing import Annotated, Literal, NotRequired, TypedDict

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

ACTIONS = Literal[
    "WAIT",
    "DRAFT_MESSAGE",
    "PROVIDE_EVIDENCE",
    "CANCEL_UNCLAIMED_PAYOUT",
    "RESERVE_EARNINGS",
    "RELEASE_RESERVE",
    "SCHEDULE_PAYOUT_RUN",
    "CREATE_REPLACEMENT_PAYOUT",
    "DEFER_SCHEDULED_PAYOUT",
    "ACCEPT_CLAIM",
    "REQUEST_OWNER",
]
FAULT_CLASSES = Literal[
    "NONE",
    "TIMEOUT_AFTER_CREATE",
    "CRASH_BETWEEN_ACCEPT_AND_RECORD",
    "PENDING_PAST_RUN",
    "ONHOLD",
    "BLOCKED",
    "UNCLAIMED_CANCEL_RACE",
    "PARTIAL_BATCH_FAILURE",
    "WEBHOOK_DUPLICATE",
    "WEBHOOK_DELAYED",
    "WEBHOOK_REORDERED",
    "WEBHOOK_FORGED",
    "STALE_APPROVAL",
    "DOUBLE_SPEND_PROPOSALS",
    "RETRY_SETTLED_OBLIGATION",
    "DISPUTE_DEADLINE_CLOSE",
    "INJECTION_REFUND_OTHER_ACCOUNT",
    "INJECTION_NEW_PAYEE",
    "INSUFFICIENT_UNRESERVED_CASH",
    "UNSTRUCTURED_DISPUTE_TRIAGE",
    "KEY_WINDOW_EXPIRED",
]
Offset = Annotated[str, Field(pattern=r"^\+[0-9]+[mhd]$")]
UTCString = Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")]
DecimalString = Annotated[str, Field(pattern=r"^[0-9]+\.[0-9]{2}$")]
Nonnegative = Annotated[int, Field(ge=0)]
Positive = Annotated[int, Field(ge=1)]


def seconds(value):
    match = re.fullmatch(r"\+([0-9]+)([mhd])", value)
    if not match:
        raise ValueError("Invalid timeline offset")
    return int(match[1]) * {"m": 60, "h": 3600, "d": 86400}[match[2]]


class Doc(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class Money(Doc):
    minor: int
    currency: Literal["USD"]


class Policy(Doc):
    maker_share_pct: Annotated[int, Field(ge=0, le=100)] = 90
    deadline_close_seconds: Positive = 172800
    refund_limit_per_order: Money
    refund_limit_per_day: Money


class Maker(Doc):
    key: str
    display_name: str
    payout_email: str
    verified: bool


class Order(Doc):
    key: str
    maker: str
    amount: Money
    capture_id: str
    captured_at: UTCString
    tracking: dict | None = None


class MockConfig(Doc):
    payout_processing_seconds: Nonnegative = 30
    key_retention_days: Positive = 30
    unclaimed_counts_in_available: bool = False
    dispute_hold: bool = True
    evidence_allowed_stages: list[str] = Field(default_factory=lambda: ["INQUIRY", "CHARGEBACK"])
    balance_refresh_seconds: Positive = 10800
    transactions_lag_seconds: Nonnegative = 10800
    webhook_delay_seconds: Nonnegative = 5
    cancel_emits: list[str] = Field(default_factory=lambda: ["CANCELED", "RETURNED"])


class PayPalBalances(Doc):
    available: DecimalString
    withheld: DecimalString
    currency_code: Literal["USD"]


class PayPalSeed(Doc):
    balances: PayPalBalances
    registered_receivers: list[str] = Field(default_factory=list)
    orders: list[dict] = Field(default_factory=list)
    captures: list[dict] = Field(default_factory=list)
    payout_items: list[dict] = Field(default_factory=list)
    disputes: list[dict] = Field(default_factory=list)
    trackers: list[dict] = Field(default_factory=list)
    config: MockConfig = Field(default_factory=MockConfig)
    repeat_transaction_ids: bool = False

    @model_validator(mode="after")
    def values(self):
        for entry in self.captures + self.payout_items:
            value = entry.get("amount")
            value = value.get("value") if isinstance(value, dict) else value
            if not isinstance(value, str) or not re.fullmatch(r"[0-9]+\.[0-9]{2}", value):
                raise ValueError("PayPal seed amounts must be two-place decimal strings")
        if any(not isinstance(entry.get("id"), str) for entry in self.captures):
            raise ValueError("Capture IDs are required")
        ids = [entry["id"] for entry in self.captures]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate capture IDs")
        return self


class PriorPayout(TypedDict):
    # Preserve the existing historical payload; only its order links are typed here.
    __pydantic_config__ = ConfigDict(strict=True, extra="allow")
    maker: str
    order_keys: NotRequired[list[str]]


class InitialState(Doc):
    policy: Policy
    makers: list[Maker]
    orders: list[Order]
    prior_payouts: list[PriorPayout] = Field(default_factory=list)
    disputes: list[dict] = Field(default_factory=list)
    paypal: PayPalSeed


class Match(Doc):
    method: Literal["GET", "POST", "DELETE"]
    path: str
    nth: Positive = 1
    count: Positive = 1

    @field_validator("path")
    @classmethod
    def valid_pattern(cls, value):
        try:
            re.compile(value)
        except re.error:
            raise ValueError("Invalid fault regex") from None
        return value


class RequestAction(Doc):
    type: Literal[
        "drop_response",
        "timeout",
        "http_error",
        "connection_reset",
        "delay",
        "test_value",
    ]
    after_commit: bool | None = None
    seconds: Nonnegative | None = None
    status: Literal[500, 503] | None = None
    body: dict | None = None
    value: str | None = None

    @model_validator(mode="after")
    def required_parameters(self):
        if self.type in {"timeout", "delay"} and self.seconds is None:
            raise ValueError("Wait action requires seconds")
        if (
            self.type in {"drop_response", "timeout", "http_error", "connection_reset"}
            and self.after_commit is None
        ):
            raise ValueError("Action requires after_commit")
        if self.type == "http_error" and self.status is None:
            raise ValueError("HTTP error requires status")
        if self.type == "test_value" and self.value is None:
            raise ValueError("Test value required")
        return self


class RequestFault(Doc):
    id: str | None = None
    match: Match
    action: RequestAction


class WebhookMatch(Doc):
    event_type: str
    nth: Positive = 1
    count: Positive = 1


class WebhookAction(Doc):
    type: Literal["drop", "duplicate", "delay", "reorder_after"]
    copies: Positive | None = None
    seconds: Nonnegative | None = None
    event_type: str | None = None

    @model_validator(mode="after")
    def parameters(self):
        if self.type == "duplicate" and self.copies is None:
            raise ValueError("Duplicate requires copies")
        if self.type == "delay" and self.seconds is None:
            raise ValueError("Delay requires seconds")
        if self.type == "reorder_after" and self.event_type is None:
            raise ValueError("Reorder requires event_type")
        return self


class WebhookFault(Doc):
    match: WebhookMatch
    action: WebhookAction


class Step(Doc):
    at: Offset


class Tick(Step):
    do: Literal["tick"]
    repeat: Positive = 1
    advance_seconds: Nonnegative = 0


class Advance(Step):
    do: Literal["advance"]
    seconds: Nonnegative


class FaultStep(Step):
    do: Literal["fault"]
    fault: RequestFault


class WebhookStep(Step):
    do: Literal["webhook_fault"]
    fault: WebhookFault


class MockStep(Step):
    do: Literal["mock"]
    method: Literal["GET", "POST", "DELETE"]
    path: Annotated[str, Field(pattern=r"^/_mock/")]
    body: dict = Field(default_factory=dict)


class PlanRun(Step):
    do: Literal["plan_payout_run"]
    week_start: Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")]


class Owner(Step):
    do: Literal["owner"]
    policy: Literal["approve_expected", "approve_all", "reject_all", "approve_after"]
    seconds: Nonnegative | None = None

    @model_validator(mode="after")
    def delay(self):
        if self.policy == "approve_after" and self.seconds is None:
            raise ValueError("approve_after requires seconds")
        return self


class BuyerMessage(Step):
    do: Literal["buyer_message"]
    dispute: str
    content: str


TimelineStep = Annotated[
    Tick | Advance | FaultStep | WebhookStep | MockStep | PlanRun | Owner | BuyerMessage,
    Field(discriminator="do"),
]


class Task(Doc):
    kind: str
    description: str


class ExpectedPayout(Doc):
    maker: str
    amount: Money
    count: Positive


class ExpectedApproval(Doc):
    action: ACTIONS
    total: Money | None = None


class Expected(Doc):
    payouts: list[ExpectedPayout]
    no_payout_to: list[str]
    required_actions: list[ACTIONS]
    owner_should_approve: list[ExpectedApproval]
    refunds: list[dict]
    max_owner_escalations: Nonnegative
    deadline: Offset


class Scenario(Doc):
    id: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]+$")]
    title: str
    fault_class: FAULT_CLASSES
    provenance: Literal["recorded", "synthesised", "mixed"]
    ground: Literal["mock", "sandbox"]
    seed: int
    start: UTCString
    initial_state: InitialState
    timeline: list[TimelineStep]
    task: Task
    expected: Expected

    @field_validator("start")
    @classmethod
    def valid_time(cls, value):
        from datetime import datetime

        datetime.fromisoformat(value)
        return value

    @model_validator(mode="after")
    def references(self):
        makers = {m.key for m in self.initial_state.makers}
        orders = {o.key for o in self.initial_state.orders}
        if len(makers) != len(self.initial_state.makers) or len(orders) != len(
            self.initial_state.orders
        ):
            raise ValueError("Duplicate maker/order keys")
        if any(o.maker not in makers for o in self.initial_state.orders):
            raise ValueError("Unknown order maker")
        for payout in self.initial_state.prior_payouts:
            if payout.get("maker") not in makers:
                raise ValueError("Unknown prior payout maker")
            linked = payout.get("order_keys", [])
            if len(linked) != len(set(linked)):
                raise ValueError("Duplicate prior payout order keys")
            if set(linked) - orders:
                raise ValueError("Unknown prior payout order")
            if any(
                order.maker != payout["maker"]
                for order in self.initial_state.orders
                if order.key in linked
            ):
                raise ValueError("Prior payout order belongs to another maker")
        for dispute in self.initial_state.disputes:
            if "order" in dispute and dispute["order"] not in orders:
                raise ValueError("Unknown disputed order")
        if (
            any(p.maker not in makers for p in self.expected.payouts)
            or set(self.expected.no_payout_to) - makers
        ):
            raise ValueError("Unknown expected maker")
        if any(p.amount.minor <= 0 for p in self.expected.payouts) or any(
            o.amount.minor <= 0 for o in self.initial_state.orders
        ):
            raise ValueError("Amounts must be positive")
        offsets = [seconds(step.at) for step in self.timeline]
        if offsets != sorted(offsets):
            raise ValueError("Timeline offsets must be nondecreasing")
        if self.ground == "sandbox" and any(
            step.do in {"fault", "webhook_fault", "mock"} for step in self.timeline
        ):
            raise ValueError("Sandbox timeline cannot call mock controls")
        captures = {c["id"] for c in self.initial_state.paypal.captures}
        if self.ground == "mock" and any(
            o.capture_id not in captures for o in self.initial_state.orders
        ):
            raise ValueError("Order capture is not seeded")
        disputes = {d.get("key", d.get("paypal_dispute_id")) for d in self.initial_state.disputes}
        disputes |= {d.get("dispute_id") for d in self.initial_state.paypal.disputes}
        if any(
            step.dispute not in disputes for step in self.timeline if isinstance(step, BuyerMessage)
        ):
            raise ValueError("Unknown buyer-message dispute")
        if self.provenance == "recorded" and synthesised_reasons(self):
            raise ValueError("Recorded provenance cannot rely on synthesised behaviour")
        return self


# F13: no sandbox recording or test value produces these item states.
SYNTHESISED_ITEM_STATES = {"ONHOLD", "BLOCKED", "FAILED", "REFUNDED", "REVERSED"}
SYNTHESISED_CONTROLS = ("/transition", "/claim", "/payout-outcomes", "/webhooks/forge")


def synthesised_reasons(scenario: "Scenario") -> list[str]:
    """Why a scenario is not purely built from recorded sandbox behaviour (M14)."""
    reasons = []
    for item in scenario.initial_state.paypal.payout_items:
        if item.get("synthesised") or item.get("status") in SYNTHESISED_ITEM_STATES:
            reasons.append("seeded synthesised payout item")
    for step in scenario.timeline:
        if isinstance(step, FaultStep) and step.fault.action.type != "test_value":
            reasons.append("injected transport fault")
        elif isinstance(step, WebhookStep):
            reasons.append("injected webhook fault")
        elif isinstance(step, MockStep) and any(c in step.path for c in SYNTHESISED_CONTROLS):
            reasons.append("synthesised mock control")
    return reasons


def validate(data):
    return Scenario.model_validate(data)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*")
    parser.add_argument("--write-schema", action="store_true")
    args = parser.parse_args(argv)
    folder = Path(__file__).resolve().parent
    if args.write_schema:
        schema = Scenario.model_json_schema()
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["title"] = "CounterSign scenario · CONTRACTS v1.0.3"
        (folder / "scenario.schema.json").write_text(
            json.dumps(schema, indent=2) + "\n", encoding="utf-8"
        )
    paths = (
        [Path(p) for p in args.paths]
        if args.paths
        else sorted((folder / "scenarios").glob("*.json"))
    )
    errors = 0
    for path in paths:
        try:
            doc = validate(json.loads(path.read_text(encoding="utf-8")))
            if path.stem != doc.id:
                raise ValueError("Filename must match scenario ID")
            print(f"{doc.id}: valid ({doc.ground}, {doc.provenance})")
        except (OSError, ValueError, ValidationError) as error:
            errors += 1
            # Never include raw scenario input or buyer messages in diagnostics.
            print(f"{path.name}: INVALID ({type(error).__name__})")
    if not paths:
        print("No scenarios found")
        return 1
    return int(errors > 0)


if __name__ == "__main__":
    raise SystemExit(main())
