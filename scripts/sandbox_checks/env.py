"""Runtime-only sandbox configuration. Errors and repr never contain values."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


class SettingsError(ValueError):
    """Invalid or incomplete settings, described by variable names only."""


@dataclass(frozen=True, repr=False)
class SandboxSettings:
    client_id: str
    client_secret: str
    paypal_env: str = "sandbox"
    webhook_id: str = ""
    buyer_email: str = ""
    maker_a_email: str = ""
    maker_b_email: str = ""
    maker_c_email: str = ""
    unregistered_emails: tuple[str, ...] = ()
    database_url: str = ""

    def __post_init__(self):
        if self.paypal_env != "sandbox":
            raise SettingsError("PAYPAL_ENV must select the sandbox environment.")
        missing = [
            name
            for name, value in (
                ("PAYPAL_CLIENT_ID", self.client_id),
                ("PAYPAL_CLIENT_SECRET", self.client_secret),
            )
            if not value.strip()
        ]
        if missing:
            raise SettingsError("Missing required settings: " + ", ".join(missing))

    def __repr__(self):
        return "SandboxSettings(paypal_env='sandbox', values=<redacted>)"

    @property
    def email_aliases(self) -> dict[str, str]:
        roles = (
            ("buyer-1", self.buyer_email),
            ("maker-a", self.maker_a_email),
            ("maker-b", self.maker_b_email),
            ("maker-c", self.maker_c_email),
            *(
                (f"unreg-{index}", email)
                for index, email in enumerate(self.unregistered_emails, start=1)
            ),
        )
        return {email: f"{role}@redacted" for role, email in roles if email}


def load_settings(env_path: str | Path = ".env") -> SandboxSettings:
    """Load the local file only when a human invokes a sandbox check."""
    load_dotenv(dotenv_path=env_path, override=False, interpolate=False)
    return SandboxSettings(
        paypal_env=os.environ.get("PAYPAL_ENV", "sandbox").strip(),
        client_id=os.environ.get("PAYPAL_CLIENT_ID", "").strip(),
        client_secret=os.environ.get("PAYPAL_CLIENT_SECRET", "").strip(),
        webhook_id=os.environ.get("PAYPAL_WEBHOOK_ID", "").strip(),
        buyer_email=os.environ.get("SBX_BUYER_EMAIL", "").strip(),
        maker_a_email=os.environ.get("SBX_MAKER_A_EMAIL", "").strip(),
        maker_b_email=os.environ.get("SBX_MAKER_B_EMAIL", "").strip(),
        maker_c_email=os.environ.get("SBX_MAKER_C_EMAIL", "").strip(),
        unregistered_emails=tuple(
            email.strip()
            for email in os.environ.get("SBX_UNREG_EMAILS", "").split(",")
            if email.strip()
        ),
        database_url=os.environ.get("DATABASE_URL", "").strip(),
    )
