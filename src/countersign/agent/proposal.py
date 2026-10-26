"""Validate the single proposal returned by an untrusted model."""

import copy
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field

from countersign.agent.params import ProposalError, validate_action_params
from countersign.agent.safety import SafetyError, sanitize
from countersign.shield.snapshot import Action

FIELDS = frozenset(
    {"incident_id", "action", "params", "evidence_refs", "rationale", "expected_effect"}
)
FLAG_TYPES = frozenset({"PROMPT_INJECTION", "PAYEE_CHANGE_REQUEST", "SUSPICIOUS_EVIDENCE"})
_CURRENCY = re.compile(r"[A-Z]{3}\Z")


def _plain(value):
    if type(value) is float or not (value is None or type(value) in (str, bool, int, dict, list)):
        raise ProposalError("INVALID_PROPOSAL")
    if type(value) is dict:
        if any(type(key) is not str for key in value):
            raise ProposalError("INVALID_PROPOSAL")
        for inner in value.values():
            _plain(inner)
    elif type(value) is list:
        for inner in value:
            _plain(inner)


def _effect(value):
    if type(value) is not dict:
        raise ProposalError("INVALID_PROPOSAL_EFFECT")
    if set(value) == {"available_change", "currency"}:
        value = {
            "available_change": {"minor": value["available_change"], "currency": value["currency"]}
        }
    if set(value) != {"available_change"}:
        raise ProposalError("INVALID_PROPOSAL_EFFECT")
    amount = value["available_change"]
    if (
        type(amount) is not dict
        or set(amount) != {"minor", "currency"}
        or type(amount["minor"]) is not int
        or not -(2**63) <= amount["minor"] < 2**63
        or type(amount["currency"]) is not str
        or not _CURRENCY.fullmatch(amount["currency"])
    ):
        raise ProposalError("INVALID_PROPOSAL_EFFECT")
    return value


@dataclass(frozen=True, repr=False)
class StructuredProposal:
    incident_id: str
    action: str
    params: dict
    evidence_refs: list[str]
    rationale: str
    expected_effect: dict
    flags: tuple[dict, ...] = ()
    raw_output: dict = field(default_factory=dict)

    def __repr__(self):
        return "StructuredProposal(values=<REDACTED>)"

    def to_dict(self):
        return copy.deepcopy({name: getattr(self, name) for name in FIELDS})

    @classmethod
    def parse(cls, value, *, incident_id, flags=(), secrets=()):
        try:
            if (
                type(value) is not dict
                or not FIELDS <= value.keys()
                or value.keys() - FIELDS - {"flags"}
            ):
                raise ProposalError("INVALID_PROPOSAL")
            # Enforce bounded JSON before recursively validating numeric types.
            safe = sanitize(value, secrets)
            if type(value["incident_id"]) is not str or value["incident_id"] != incident_id:
                raise ProposalError("PROPOSAL_INCIDENT_MISMATCH")
            if type(value["action"]) is not str or value["action"] not in set(Action):
                raise ProposalError("INVALID_PROPOSAL_ACTION")
            if type(value["params"]) is not dict:
                validate_action_params(value["action"], value["params"], ())
            refs = safe["evidence_refs"]
            if (
                type(refs) is not list
                or len(refs) > 100
                or any(type(ref) is not str or not ref or len(ref) > 512 for ref in refs)
            ):
                raise ProposalError("INVALID_PROPOSAL_EVIDENCE")
            if (
                type(safe["rationale"]) is not str
                or not safe["rationale"].strip()
                or len(safe["rationale"]) > 4096
            ):
                raise ProposalError("INVALID_PROPOSAL_RATIONALE")
            effect = _effect(safe["expected_effect"])
            supplied_flags = value.get("flags", [])
            if type(supplied_flags) is not list or len(supplied_flags) > 100:
                raise ProposalError("INVALID_PROPOSAL_FLAGS")
            combined = []
            for flag in (*flags, *supplied_flags):
                if (
                    not isinstance(flag, Mapping)
                    or set(flag) != {"type", "source", "excerpt"}
                    or flag["type"] not in FLAG_TYPES
                    or any(type(flag[key]) is not str for key in flag)
                    or not flag["source"]
                    or len(flag["source"]) > 512
                    or len(flag["excerpt"]) > 4096
                ):
                    raise ProposalError("INVALID_PROPOSAL_FLAGS")
                clean = sanitize(dict(flag), secrets)
                if clean not in combined:
                    combined.append(clean)
            raw = copy.deepcopy(safe)
            raw["flags"] = combined
            validate_action_params(safe["action"], safe["params"], refs)
            _plain(value)
            return cls(
                safe["incident_id"],
                safe["action"],
                safe["params"],
                refs,
                safe["rationale"],
                effect,
                tuple(combined),
                raw,
            )
        except (SafetyError, RecursionError, TypeError, KeyError, OverflowError):
            raise ProposalError("INVALID_PROPOSAL") from None


def parse_proposal(text, *, incident_id, flags=(), secrets=()):
    """Decode strict JSON (including duplicate-key rejection), then validate."""

    def object_pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ProposalError("INVALID_PROPOSAL")
            result[key] = value
        return result

    try:
        value = json.loads(text, object_pairs_hook=object_pairs) if isinstance(text, str) else text
        return StructuredProposal.parse(
            value, incident_id=incident_id, flags=flags, secrets=secrets
        )
    except (json.JSONDecodeError, RecursionError, TypeError):
        raise ProposalError("INVALID_PROPOSAL") from None
