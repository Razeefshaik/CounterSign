"""Trusted action shapes shared by the prompt and model-output validation.

These checks know no balances, records, permissions or policy. The shield and
locked approval binding remain the authorities for every proposed action.
"""

import re
from dataclasses import dataclass, replace
from types import MappingProxyType

from countersign.shield.rules.payee_integrity import PAYEE_KEYS
from countersign.shield.snapshot import NON_FINANCIAL, Action


class ProposalError(ValueError):
    """Invalid output without echoing model content."""


class ProposalParamsError(ProposalError):
    def __init__(self, action):
        super().__init__("INVALID_PROPOSAL_PARAMS")
        self.action = str(action)


@dataclass(frozen=True)
class ParamSpec:
    required: tuple[str, ...] = ()
    optional: tuple[str, ...] = ()
    policy_only: tuple[str, ...] = ()
    description: str = ""

    @property
    def allowed(self):
        return frozenset((*self.required, *self.optional, *self.policy_only))

    def prompt_line(self, action):
        return (f"{action}: params required={list(self.required)}, "
                f"optional={list(self.optional)}. " + self.description)


_SPECS = {
    Action.WAIT: ParamSpec(description="Use {}. available_change.minor = 0; await observed facts."),
    Action.DRAFT_MESSAGE: ParamSpec(optional=("message",), description=
        "Use {} or message (string); this records a draft, never sends it. available_change.minor = 0."),
    Action.PROVIDE_EVIDENCE: ParamSpec(required=("paypal_dispute_id",),
        optional=("dispute_id", "order_id", "evidences"), description=
        'Prefer {"paypal_dispute_id":"<PayPal dispute ID>"}; dispute_id/order_id are internal IDs. '
        "Omit evidences to use stored tracking; if supplied, it must equal stored PROOF_OF_FULFILLMENT "
        "evidence_info.tracking_info (carrier_name, tracking_number); no notes or free text. "
        "evidence_refs: capture:<paypal_capture_id>, order:<internal or PayPal order ID>, "
        "tracking:<number>; at least one tracking: is required. available_change.minor = 0."),
    Action.CANCEL_UNCLAIMED_PAYOUT: ParamSpec(required=("payout_item_id",), optional=("attempt_id",), description=
        'Prefer {"payout_item_id":"<PayPal item ID>"}; attempt_id is internal. '
        "available_change.minor is positive for the returned payout amount; GET must confirm RETURNED."),
    Action.RESERVE_EARNINGS: ParamSpec(required=("obligation_id",),
        optional=("dispute_id", "paypal_dispute_id"), description=
        "obligation_id is internal; dispute references are optional. available_change.minor = 0 "
        "(earmarks are not cash). Currently recorded/deferred; not an executor write."),
    Action.RELEASE_RESERVE: ParamSpec(required=("obligation_id",),
        optional=("dispute_id", "paypal_dispute_id"), description=
        "obligation_id is internal; dispute references are optional. available_change.minor = 0 "
        "(earmarks are not cash). Currently recorded/deferred; not an executor write."),
    Action.SCHEDULE_PAYOUT_RUN: ParamSpec(required=("run_id",), optional=("lines", "replacements"),
        description="run_id is internal. Reuse the planned manifest: lines contain line_id, maker_id, "
        "amount {minor,currency}; replacements also contain attempt_no. Never invent a payee. "
        "available_change.minor is negative for the complete planned payout total; owner required."),
    Action.CREATE_REPLACEMENT_PAYOUT: ParamSpec(required=("line_id",), description=
        "line_id is internal. A new attempt on the same returned line uses owner-verified payee records. "
        "available_change.minor is negative for the payout amount; owner required."),
    Action.DEFER_SCHEDULED_PAYOUT: ParamSpec(required=("line_id",), description=
        "line_id is internal. available_change.minor = 0; owner required. "
        "Currently recorded/deferred; only an unsent line qualifies, not a sent payout."),
    Action.ACCEPT_CLAIM: ParamSpec(required=("paypal_dispute_id",),
        optional=("dispute_id", "amount", "refund_amount"), description=
        'Prefer {"paypal_dispute_id":"<PayPal dispute ID>"}; dispute_id is internal. '
        "Optional amount/refund_amount use {minor,currency} and never reduce the full claim refund. "
        "available_change.minor is negative for the full dispute amount; owner required at any amount. "
        "Never supply a refund destination or note."),
    Action.REQUEST_OWNER: ParamSpec(optional=("question",), description=
        "Use {} or question (string); this records an owner question, never permission. "
        "available_change.minor = 0."),
}
ACTION_PARAMS = MappingProxyType({
    action: replace(spec, policy_only=tuple(sorted(PAYEE_KEYS)))
    if action not in NON_FINANCIAL else spec
    for action, spec in _SPECS.items()
})

EVIDENCE_PREFIXES = frozenset({"order", "capture", "tracking"})
REFERENCE_PREFIXES = EVIDENCE_PREFIXES | frozenset({
    "dispute", "payout_item", "line", "attempt", "run", "obligation", "maker", "reservation"
})
_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")


def _money(value):
    return (type(value) is dict and set(value) == {"minor", "currency"}
            and type(value["minor"]) is int and -(2**63) <= value["minor"] < 2**63
            and type(value["currency"]) is str and re.fullmatch("[A-Z]{3}", value["currency"]))


def _evidences(value):
    if type(value) is not list or not value or len(value) > 100:
        return False
    for row in value:
        if (type(row) is not dict or set(row) != {"evidence_type", "evidence_info"}
                or row["evidence_type"] != "PROOF_OF_FULFILLMENT"
                or type(row["evidence_info"]) is not dict
                or set(row["evidence_info"]) != {"tracking_info"}):
            return False
        tracking = row["evidence_info"]["tracking_info"]
        if type(tracking) is not list or not tracking or len(tracking) > 100:
            return False
        if any(type(item) is not dict or set(item) != {"carrier_name", "tracking_number"}
               or any(type(v) is not str or not v.strip() or len(v) > 512 for v in item.values())
               for item in tracking):
            return False
    return True


def validate_action_params(action, params, refs):
    """Check syntax only; never prove an ID exists or that an action is permitted."""
    spec = ACTION_PARAMS[action]
    if (type(params) is not dict or not set(spec.required) <= params.keys()
            or not params.keys() <= spec.allowed):
        raise ProposalParamsError(action)
    for key, value in params.items():
        if key in spec.policy_only:
            # These recognized unsafe fields always reach PAYEE_INTEGRITY's
            # BLOCK, preserving auditable refusals instead of judging policy here.
            valid = True
        elif key.endswith("_id"):
            valid = type(value) is str and _ID.fullmatch(value)
        elif key in {"message", "question"}:
            valid = type(value) is str and bool(value.strip()) and len(value) <= 4096
        elif key in {"amount", "refund_amount"}:
            valid = _money(value)
        elif key == "evidences":
            valid = _evidences(value)
        else:
            valid = type(value) is list and len(value) <= 100
            if valid:
                fields = {"line_id", "maker_id", "amount"} | (
                    {"attempt_no"} if key == "replacements" else set())
                valid = all(
                    type(row) is dict and set(row) == fields
                    and all(type(row[k]) is str and _ID.fullmatch(row[k])
                            for k in ("line_id", "maker_id")) and _money(row["amount"])
                    and (key != "replacements" or type(row["attempt_no"]) is int)
                    for row in value
                )
        if not valid:
            raise ProposalParamsError(action)
    prefixes = EVIDENCE_PREFIXES if action == Action.PROVIDE_EVIDENCE else REFERENCE_PREFIXES
    if (any(":" not in ref or ref.partition(":")[0] not in prefixes
            or not ref.partition(":")[2].strip() for ref in refs)
            or (action == Action.PROVIDE_EVIDENCE
                and not any(ref.startswith("tracking:") for ref in refs))):
        raise ProposalParamsError(action)
