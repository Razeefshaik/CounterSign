"""Failed-turn diagnostics stay local, redacted and outside tracked recordings."""

import json
import re
from pathlib import Path
from uuid import uuid4

from countersign.agent.models import ModelError
from countersign.agent.safety import sanitize

DIRECTORY = Path(__file__).resolve().parents[3] / ".venv/c67-diagnostics"
_EMAIL = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+")
_KEY = re.compile(r"\b(?:AIza[A-Za-z0-9_-]{30,}|sk-(?:proj-)?[A-Za-z0-9_-]{20,}|"
                  r"gh[pousr]_[A-Za-z0-9]{20,}|AKIA[A-Z0-9]{16})\b")


class CLIFailure(ModelError):
    def __init__(self, code, detail=""):
        super().__init__(code)  # str/repr expose the fixed code only.
        self.local_detail = detail if isinstance(detail, str) else ""


def provider_code(value):
    if (isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", value)
            and not _KEY.search(value) and sanitize(value) == value):
        return value
    return None


def provider_error(message):
    """CLI transport failures may embed the provider's JSON error envelope."""
    if isinstance(message, str):
        match = re.search(r'\{\s*"error"\s*:', message[:16000])
        if match:
            try:
                value, _ = json.JSONDecoder().raw_decode(message[match.start():16000])
                if isinstance(value.get("error"), dict):
                    return value["error"]
            except (ValueError, TypeError, AttributeError):
                pass
    return {}


def save_failure(error, *, secrets=()):
    # Never called for a startup warning or a successful turn.
    detail = getattr(error, "local_detail", "") or str(error)
    embedded = provider_error(detail).get("message")
    if isinstance(embedded, str):
        detail = embedded
    text = sanitize(detail[:16000], secrets)
    text = _EMAIL.sub("[REDACTED]", _KEY.sub("[REDACTED]", text)).replace("<REDACTED>", "[REDACTED]")[:500]
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    path = DIRECTORY / f"failed-turn-{uuid4().hex}.txt"
    path.write_text(text, encoding="utf-8", newline="\n")
    return path
