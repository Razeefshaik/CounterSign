"""Sandbox/local-only HTTP transport and neutral, safe-to-repr result types.

Bodies remain available to trusted application code. Use ``safe_body`` for audit
records: the original body may contain buyer or payee emails. There is no request
logging, automatic retry or redirect following in this module.
"""

import asyncio
import base64
import hashlib
import json
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, unquote, urlsplit

import httpx

from countersign.config import PayPalSettings, paypal_base_url
from countersign.money import Money, MoneyError

_EMAIL = re.compile(r"[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)
_PRIVATE = re.compile(
    r"authorization|auth.assertion|access.token|refresh.token|client.id|client.secret|"
    r"password|cookie|transmission.sig|owner.token|^token$|api.key|jwt",
    re.IGNORECASE,
)


class PayPalError(RuntimeError):
    """Safe error text; private requests and server messages are never attached."""


class UnsafePayPalURL(PayPalError):
    pass


class PayPalTransportError(PayPalError):
    pass


class PayPalAuthError(PayPalError):
    pass


class PayPalDataError(PayPalError):
    pass


def canonical_payload(payload: Mapping) -> bytes:
    """Stable UTF-8 representation shared by approval and executor boundaries."""
    try:
        return json.dumps(
            dict(payload),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode()
    except (TypeError, ValueError, OverflowError):
        raise PayPalDataError("Operation payload must be canonical JSON") from None


def canonical_payload_hash(payload: Mapping) -> str:
    return "sha256:" + hashlib.sha256(canonical_payload(payload)).hexdigest()


class Redactor:
    """Small runtime redactor, independent of the sandbox recording scripts."""

    def __init__(self, settings: PayPalSettings):
        basic = base64.b64encode(f"{settings.client_id}:{settings.client_secret}".encode()).decode()
        self._secrets = set()
        for value in (settings.client_id, settings.client_secret, settings.webhook_id, basic):
            self.add_secret(value)

    def __repr__(self):
        return "Redactor(values=<REDACTED>)"

    def add_secret(self, value: str):
        if value:
            self._secrets.update((value, quote(value, safe="")))

    def text(self, value: str) -> str:
        for secret in sorted(self._secrets, key=len, reverse=True):
            value = value.replace(secret, "<REDACTED>")
        return _EMAIL.sub("<EMAIL>", value)

    def _discover(self, value):
        if isinstance(value, Mapping):
            for key, item in value.items():
                if _PRIVATE.search(str(key)) and isinstance(item, str):
                    self.add_secret(item)
                self._discover(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                self._discover(item)

    def redact(self, value):
        self._discover(value)
        return self._clean(value)

    def _clean(self, value):
        if isinstance(value, Mapping):
            return {
                self.text(str(key)): "<REDACTED>"
                if _PRIVATE.search(str(key))
                else self._clean(item)
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [self._clean(item) for item in value]
        if isinstance(value, str):
            return self.text(value)
        return value


@dataclass(frozen=True)
class HTTPResponse:
    status_code: int
    body: Any = field(repr=False)
    debug_id: str | None = None
    safe_body: Any = field(default=None, repr=False)
    amounts: Mapping[str, Money] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class Ok:
    resp: HTTPResponse


@dataclass(frozen=True)
class DefiniteError:
    resp: HTTPResponse


@dataclass(frozen=True)
class Ambiguous:
    reason: str
    resp: HTTPResponse | None = None
    request_id: str | None = None
    original_batch_link: str | None = None


def resource_id(value: str) -> str:
    """No IDs become paths, URLs or query strings supplied by untrusted data."""
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise PayPalDataError("Invalid PayPal resource ID")
    return value


def parse_amounts(body: Any) -> dict[str, Money]:
    """Validate every PayPal decimal amount, retaining raw fields unchanged."""
    found: dict[str, Money] = {}

    def visit(value, path):
        if isinstance(value, Mapping):
            if "value" in value and ("currency" in value or "currency_code" in value):
                try:
                    found[path] = Money.from_paypal(value)
                except (MoneyError, TypeError, ValueError):
                    raise PayPalDataError("Malformed PayPal money amount") from None
            for key, item in value.items():
                visit(item, f"{path}/{key}")
        elif isinstance(value, list):
            for index, item in enumerate(value):
                visit(item, f"{path}/{index}")

    visit(body, "")
    return found


class PayPalHTTP:
    def __init__(
        self,
        settings: PayPalSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 30,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        self.settings = settings
        self.base_url = paypal_base_url(settings.base_url)
        self.redactor = Redactor(settings)
        self._client = httpx.AsyncClient(
            transport=transport, timeout=timeout, follow_redirects=False, trust_env=False
        )
        self._monotonic = monotonic
        self._token = ""
        self._expires = 0
        self._auth_lock = asyncio.Lock()

    def __repr__(self):
        return "PayPalHTTP(values=<REDACTED>)"

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.aclose()

    async def aclose(self):
        await self._client.aclose()

    def url(self, path: str) -> str:
        """Resolve only relative API paths or same-origin HATEOAS URLs."""
        try:
            parsed = urlsplit(path)
            origin = urlsplit(self.base_url)
            decoded = path
            for _ in range(3):
                decoded = unquote(decoded)
            decoded_path = urlsplit(decoded).path
            valid = (
                isinstance(path, str)
                and not parsed.fragment
                and not parsed.username
                and not parsed.password
                and not any(ord(char) < 32 for char in path)
                and "\\" not in decoded
                and not decoded_path.startswith("//")
                and not any(part in {".", ".."} for part in decoded_path.split("/"))
            )
            if parsed.scheme or parsed.netloc:
                valid = valid and (
                    parsed.scheme == origin.scheme
                    and parsed.hostname == origin.hostname
                    and (parsed.port or (443 if parsed.scheme == "https" else 80))
                    == (origin.port or (443 if origin.scheme == "https" else 80))
                )
            else:
                valid = valid and path.startswith("/") and not path.startswith("//")
            valid = valid and parsed.path.startswith("/v")
        except (AttributeError, TypeError, ValueError):
            valid = False
        if not valid:
            raise UnsafePayPalURL("Only same-origin PayPal API paths are allowed") from None
        return path if parsed.scheme else self.base_url + path

    async def _send(self, method, url, **kwargs):
        try:
            return await self._client.request(method, url, **kwargs)
        except (httpx.HTTPError, OSError) as exc:
            raise PayPalTransportError(type(exc).__name__) from None

    async def _authenticate(self):
        async with self._auth_lock:
            if self._token and self._monotonic() < self._expires:
                return
            response = await self._send(
                "POST",
                self.url("/v1/oauth2/token"),
                auth=httpx.BasicAuth(self.settings.client_id, self.settings.client_secret),
                data={"grant_type": "client_credentials"},
            )
            try:
                body = response.json()
                token, lifetime = body.get("access_token"), body.get("expires_in")
                valid = (
                    200 <= response.status_code < 300
                    and isinstance(token, str)
                    and bool(token)
                    and isinstance(lifetime, int)
                    and not isinstance(lifetime, bool)
                    and lifetime > 0
                    and body.get("token_type", "Bearer").lower() == "bearer"
                )
            except (ValueError, AttributeError, TypeError):
                valid = False
            if not valid:
                raise PayPalAuthError("PayPal OAuth failed") from None
            self.redactor.add_secret(token)
            self._token = token
            self._expires = self._monotonic() + max(1, lifetime - min(30, lifetime // 2))

    async def request(
        self, method: str, path: str, *, request_id: str | None = None, **kwargs
    ) -> HTTPResponse:
        url = self.url(path)  # Validate before even acquiring a token.
        if method not in {"GET", "POST"}:
            raise PayPalDataError("Unsupported PayPal method")
        if set(kwargs) - {"json", "data", "files", "params", "content"}:
            raise PayPalDataError("Caller cannot replace PayPal transport authentication")
        headers = {}
        if "json" in kwargs:
            headers["Content-Type"] = "application/json"
            kwargs["content"] = json.dumps(
                kwargs.pop("json"),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode()
        if request_id is not None:
            headers["PayPal-Request-Id"] = resource_id(request_id)
        await self._authenticate()
        headers["Authorization"] = f"Bearer {self._token}"
        response = await self._send(method, url, headers=headers, **kwargs)
        try:
            body = response.json() if response.content else None
        except (json.JSONDecodeError, UnicodeError, ValueError):
            body = None
        debug = response.headers.get("paypal-debug-id")
        if debug:
            debug = self.redactor.text(debug)
            if len(debug) > 128 or not re.fullmatch(r"[A-Za-z0-9_-]+", debug):
                debug = None
        return HTTPResponse(response.status_code, body, debug, self.redactor.redact(body))
