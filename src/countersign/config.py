"""Runtime-only configuration: no file reads or private values in representations."""

import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit

SANDBOX_URL = "https://api-m.sandbox.paypal.com"
MOCK_URL = "http://127.0.0.1:8099"


class SettingsError(ValueError):
    """Invalid configuration without including its private value."""


def paypal_base_url(value: str) -> str:
    """Permit only the sandbox HTTPS origin or an explicit loopback mock origin."""
    try:
        parsed = urlsplit(value)
        port = parsed.port
        allowed = (
            parsed.scheme in {"http", "https"}
            and parsed.username is None
            and parsed.password is None
            and not parsed.query
            and not parsed.fragment
            and parsed.path in {"", "/"}
            and (port is None or 1 <= port <= 65535)
            and (
                (
                    parsed.hostname == "api-m.sandbox.paypal.com"
                    and parsed.scheme == "https"
                    and port in {None, 443}
                )
                or parsed.hostname in {"127.0.0.1", "localhost"}
            )
        )
    except (ValueError, TypeError, AttributeError):
        allowed = False
    if not allowed:
        raise SettingsError(
            "PAYPAL_BASE_URL must be the sandbox HTTPS origin or a local mock"
        ) from None
    suffix = f":{port}" if port is not None else ""
    return f"{parsed.scheme}://{parsed.hostname}{suffix}"


@dataclass(frozen=True)
class PayPalBehavior:
    """Single home for pending D6/D7/D9/D10 adapter defaults."""

    format_rejections: tuple[int, ...] = (400, 415, 422)
    evidence_format: str = "multipart"
    accept_claim_format: str = "json"
    refund_source: str | None = None
    ambiguous_error_names: frozenset[str] = frozenset(
        {
            "DUPLICATE_SENDER_BATCH_ID",
            "DUPLICATE_BATCH_ID",
            "DUPLICATE_REQUEST_ID",
            "PREVIOUS_REQUEST_IN_PROGRESS",
            "REQUEST_TIMEOUT_EXCEEDED",
            "INTERNAL_ERROR",
        }
    )
    reconcile_original_batch_links: bool = True

    def __post_init__(self):
        if self.refund_source not in {None, "paypal:available", "paypal:withheld"}:
            raise SettingsError("Refund source must be available or withheld")
        if self.evidence_format not in {"multipart", "json"} or self.accept_claim_format not in {
            "multipart",
            "json",
        }:
            raise SettingsError("PayPal body formats must be multipart or json")
        if any(type(code) is not int or not 400 <= code < 500 for code in self.format_rejections):
            raise SettingsError("Format rejection codes must be HTTP 4xx integers")


@dataclass(frozen=True, repr=False)
class PayPalSettings:
    base_url: str = MOCK_URL
    client_id: str = field(default="mock-client", repr=False)
    client_secret: str = field(default="mock-secret", repr=False)
    webhook_id: str = field(default="", repr=False)
    behavior: PayPalBehavior = field(default_factory=PayPalBehavior)

    def __post_init__(self):
        object.__setattr__(self, "base_url", paypal_base_url(self.base_url))
        if bool(self.client_id) != bool(self.client_secret):
            raise SettingsError("PayPal client ID and secret must be supplied together")

    @property
    def is_mock(self) -> bool:
        return urlsplit(self.base_url).hostname in {"127.0.0.1", "localhost"}

    def __repr__(self):
        return "PayPalSettings(values=<REDACTED>)"


@dataclass(frozen=True, repr=False)
class Settings:
    database_url: str = field(repr=False)
    mode: str = "demo"
    paypal: PayPalSettings = field(default_factory=PayPalSettings, repr=False)
    owner_token: str = field(default="", repr=False)

    def __post_init__(self):
        try:
            parsed = urlsplit(self.database_url)
            valid = parsed.scheme in {"postgresql", "postgres"} and bool(parsed.path.strip("/"))
        except (ValueError, TypeError, AttributeError):
            valid = False
        if not valid:
            raise SettingsError("DATABASE_URL must be a PostgreSQL URL naming a database") from None
        if self.mode not in {"demo", "live"}:
            raise SettingsError("COUNTERSIGN_MODE must be demo or live")
        if (self.mode == "demo") != self.paypal.is_mock:
            raise SettingsError("Demo mode requires the local mock; live mode requires the sandbox")
        if self.mode == "live" and (
            not self.owner_token.strip()
            or not self.paypal.client_id.strip()
            or not self.paypal.client_secret.strip()
        ):
            raise SettingsError("Live mode requires OWNER_TOKEN and PayPal credentials")

    def __repr__(self):
        return "Settings(values=<REDACTED>)"


def load_settings() -> Settings:
    """Read process environment only when called. Demo never reads sandbox credentials."""
    database = os.environ.get("DATABASE_URL", "").strip()
    if not database:
        raise SettingsError("DATABASE_URL is required")
    mode = os.environ.get("COUNTERSIGN_MODE", "demo").strip()
    mock = mode == "demo"
    paypal = PayPalSettings(
        base_url=os.environ.get("PAYPAL_BASE_URL", MOCK_URL if mock else SANDBOX_URL).strip(),
        client_id=os.environ.get("MOCK_PAYPAL_CLIENT_ID", "mock-client")
        if mock
        else os.environ.get("PAYPAL_CLIENT_ID", ""),
        client_secret=os.environ.get("MOCK_PAYPAL_CLIENT_SECRET", "mock-secret")
        if mock
        else os.environ.get("PAYPAL_CLIENT_SECRET", ""),
        webhook_id=os.environ.get("PAYPAL_WEBHOOK_ID", ""),
        behavior=PayPalBehavior(
            evidence_format=os.environ.get("PAYPAL_EVIDENCE_FORMAT", "multipart"),
            accept_claim_format=os.environ.get("PAYPAL_ACCEPT_CLAIM_FORMAT", "json"),
            refund_source=os.environ.get("PAYPAL_REFUND_SOURCE") or None,
        ),
    )
    return Settings(
        database, mode=mode, paypal=paypal, owner_token=os.environ.get("OWNER_TOKEN", "")
    )
