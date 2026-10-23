"""Runtime settings. This module never loads files or reads the environment on import."""

import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit


class SettingsError(ValueError):
    """Missing or invalid configuration, without including its private value."""


@dataclass(frozen=True)
class Settings:
    database_url: str = field(repr=False)

    def __post_init__(self):
        try:
            parsed = urlsplit(self.database_url)
            valid = parsed.scheme in {"postgresql", "postgres"} and bool(parsed.path.strip("/"))
        except ValueError:
            valid = False
        if not valid:
            raise SettingsError("DATABASE_URL must be a PostgreSQL URL naming a database") from None

    def __repr__(self):
        return "Settings(database_url='[REDACTED]')"


def load_settings() -> Settings:
    """Read DATABASE_URL when explicitly called; no dotenv side effects."""
    value = os.environ.get("DATABASE_URL", "").strip()
    if not value:
        raise SettingsError("DATABASE_URL is required")
    return Settings(database_url=value)
