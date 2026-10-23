"""Recursive secret masking for public recordings and human-facing output."""

import base64
import re
from collections.abc import Mapping
from urllib.parse import quote, quote_plus

from .env import SandboxSettings

REDACTED = "[REDACTED]"
EMAIL = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+")
AUTHORIZATION_KEYS = {"authorization", "proxy_authorization"}
SECRET_KEYS = {
    "access_token",
    "refresh_token",
    "id_token",
    "client_id",
    "client_secret",
    "password",
    "secret",
    "token",
    "assertion",
    "cookie",
    "set_cookie",
}


def normalized_key(key: str) -> str:
    return key.lower().replace("-", "_")


class Redactor:
    def __init__(self, *, secrets=(), email_aliases: Mapping[str, str] | None = None):
        self._secrets: set[str] = set()
        self._email_aliases = {key.lower(): value for key, value in (email_aliases or {}).items()}
        for value in secrets:
            self.add_secret(value)

    def __repr__(self):
        return "Redactor(values=<redacted>)"

    @classmethod
    def from_settings(cls, settings: SandboxSettings):
        redactor = cls()
        redactor.add_settings(settings)
        return redactor

    def add_settings(self, settings: SandboxSettings):
        basic = base64.b64encode(
            f"{settings.client_id}:{settings.client_secret}".encode()
        ).decode("ascii")
        for secret in (settings.client_id, settings.client_secret, settings.database_url, basic):
            self.add_secret(secret)
        self._email_aliases.update(
            {email.lower(): alias for email, alias in settings.email_aliases.items()}
        )

    def add_secret(self, value: str):
        if value and value != REDACTED:
            self._secrets.update((value, quote(value, safe=""), quote_plus(value)))

    def _discover_secrets(self, value):
        if isinstance(value, Mapping):
            for key, child in value.items():
                if normalized_key(str(key)) in SECRET_KEYS and isinstance(child, str):
                    self.add_secret(child)
                self._discover_secrets(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                self._discover_secrets(child)

    def text(self, value: str) -> str:
        for secret in sorted(self._secrets, key=len, reverse=True):
            value = value.replace(secret, REDACTED)
        for email, alias in self._email_aliases.items():
            for variant in (quote(email, safe=""), quote_plus(email)):
                value = re.sub(
                    re.escape(variant), lambda _, alias=alias: alias, value, flags=re.IGNORECASE
                )
        value = EMAIL.sub(
            lambda match: (
                match.group(0)
                if match.group(0).lower().endswith("@redacted")
                else self._email_aliases.get(match.group(0).lower(), "email@redacted")
            ),
            value,
        )
        return re.sub(r"(?i)\bBearer\s+[^\s\"'<>]+", f"Bearer {REDACTED}", value)

    def redact(self, value):
        """Return a sanitized JSON-compatible copy; do not mutate the input."""
        self._discover_secrets(value)
        return self._redact(value)

    def _redact(self, value):
        if isinstance(value, Mapping):
            return {
                self.text(str(key)): (
                    REDACTED if normalized_key(str(key)) in SECRET_KEYS else self._redact(child)
                )
                for key, child in value.items()
                if normalized_key(str(key)) not in AUTHORIZATION_KEYS
            }
        if isinstance(value, (list, tuple)):
            return [self._redact(child) for child in value]
        if isinstance(value, bytes):
            try:
                return self.text(value.decode("utf-8"))
            except UnicodeDecodeError:
                return "[binary body omitted]"
        if isinstance(value, str):
            return self.text(value)
        if value is None or isinstance(value, (bool, int, float)):
            return value
        raise TypeError("Unsupported recording value type.")
