"""Bound and redact data before it crosses the model or public trace boundary.

Pattern flags are investigative hints, not a complete injection detector. Tool
capabilities, proposal validation and the deterministic shield remain the guard.
"""

import json
import math
import re
from collections.abc import Mapping
from datetime import date, datetime
from uuid import UUID

REDACTED = "<REDACTED>"
_EMAIL = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_BEARER = re.compile(r"\b(?:Bearer|Basic)\s+[^\s\"'<>]+", re.IGNORECASE)
_URL_AUTH = re.compile(r"((?:https?|postgres(?:ql)?)://)[^\s/@]+:[^\s/@]+@", re.IGNORECASE)
_PRIVATE_ASSIGNMENT = re.compile(
    r"\b(access_token|refresh_token|client_secret|api_key|password)"
    r"([\"']?\s*[:=]\s*[\"']?)[^\s\"'&,;}]+", re.IGNORECASE
)
_SENSITIVE = re.compile(
    r"(?:authorization|access_?token|refresh_?token|client_?(?:id|secret)|"
    r"api_?key|password|secret|private_?key)",
    re.IGNORECASE,
)
_INJECTION = re.compile(
    r"(?:\bignore\b.{0,50}\b(?:instructions?|rules?|system|policy)\b|"
    r"\b(?:override|bypass|disable)\b.{0,45}\b(?:shield|approval|rules?|policy)\b|"
    r"\brefund\b.{0,65}\b(?:to\s+(?:this|my|the|another|a|account)|account\s+\w)|"
    r"\b(?:system|developer)\s*:\s*(?:ignore|you must|refund))",
    re.IGNORECASE,
)
_PAYEE_CHANGE = re.compile(
    r"(?:\b(?:change|replace|update|new)\b.{0,45}\b(?:payee|payout.?email|"
    r"recipient|bank.?account)\b|\b(?:pay|send|refund)\b.{0,65}"
    r"\b(?:instead|different account|new account|other account)\b)",
    re.IGNORECASE,
)


class SafetyError(ValueError):
    """A fixed error that never includes untrusted data."""


def sanitize(value, secrets=()):
    """Return bounded JSON data, masking known credentials and full emails.

    No environment reads occur here. Callers supply private values explicitly.
    Oversized/deep data is refused rather than silently changing action targets.
    """
    private = tuple(sorted({v for v in secrets if isinstance(v, str) and v}, key=len, reverse=True))
    visited = 0

    def string(item):
        if len(item) > 16384:
            raise SafetyError("AGENT_DATA_TOO_LARGE")
        for secret in private:
            item = item.replace(secret, REDACTED)
        item = _BEARER.sub(REDACTED, item)
        item = _URL_AUTH.sub(r"\1<REDACTED>@", item)
        item = _PRIVATE_ASSIGNMENT.sub(lambda match: match[1] + match[2] + REDACTED, item)
        return _EMAIL.sub(lambda match: match[0][0] + "***@" + match[0].split("@", 1)[1], item)

    def visit(item, depth=0):
        nonlocal visited
        visited += 1
        if depth > 16 or visited > 10000:
            raise SafetyError("AGENT_DATA_TOO_LARGE")
        if item is None or type(item) in (bool, int):
            return item
        if isinstance(item, str):
            return string(item)
        if isinstance(item, UUID):
            return str(item)
        if isinstance(item, datetime | date):
            return item.isoformat()
        if type(item) is float:
            if not math.isfinite(item):
                raise SafetyError("INVALID_AGENT_DATA")
            return item
        if isinstance(item, Mapping):
            result = {}
            for key, inner in item.items():
                if not isinstance(key, str):
                    raise SafetyError("INVALID_AGENT_DATA")
                safe_key = string(key)
                if safe_key in result:
                    raise SafetyError("INVALID_AGENT_DATA")
                result[safe_key] = REDACTED if _SENSITIVE.search(key) else visit(inner, depth + 1)
            return result
        if isinstance(item, list | tuple):
            return [visit(inner, depth + 1) for inner in item]
        raise SafetyError("INVALID_AGENT_DATA")

    safe = visit(value)
    if len(json.dumps(safe, ensure_ascii=False, allow_nan=False).encode()) > 131072:
        raise SafetyError("AGENT_DATA_TOO_LARGE")
    return safe


def injection_flags(value, *, source="data", secrets=()):
    """Flag suspicious destination requests or instructions in untrusted data."""
    # Validate/bound first. Match original text so redaction cannot hide an attack.
    sanitize(value, secrets)
    result = []

    def visit(item, path, depth=0):
        if isinstance(item, Mapping):
            for key, inner in item.items():
                visit(inner, f"{path}.{key}", depth + 1)
        elif isinstance(item, list | tuple):
            for index, inner in enumerate(item):
                visit(inner, f"{path}[{index}]", depth + 1)
        elif isinstance(item, str):
            kinds = []
            if _INJECTION.search(item):
                kinds.append("PROMPT_INJECTION")
            if _PAYEE_CHANGE.search(item):
                kinds.extend(("PROMPT_INJECTION", "PAYEE_CHANGE_REQUEST"))
            for kind in dict.fromkeys(kinds):
                result.append(
                    {
                        "type": kind,
                        "source": sanitize(path, secrets),
                        "excerpt": sanitize(item[:240], secrets),
                    }
                )

    visit(value, source)
    return tuple(result[:100])
