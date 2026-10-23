"""Sandbox-only HTTP client for human-operated checks. No automatic write retries."""

import asyncio
import base64
import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlsplit

import httpx

from .env import SandboxSettings
from .recorder import Recorder, utc_now

SANDBOX_BASE_URL = "https://api-m.sandbox.paypal.com"


class SandboxRequestError(RuntimeError):
    """An exchange failed; only redacted evidence and an error type are exposed."""


class SandboxAuthError(SandboxRequestError):
    """Authentication failed without disclosing credentials or the token."""


@dataclass(frozen=True)
class AuthInfo:
    scope: str
    expires_in: int


def body_value(body: bytes, content_type: str):
    if "json" in content_type:
        try:
            return json.loads(body)
        except (ValueError, UnicodeDecodeError):
            pass
    return body


class SandboxClient:
    def __init__(
        self,
        settings: SandboxSettings,
        recorder: Recorder,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float | httpx.Timeout = 30.0,
        clock: Callable[[], datetime] = utc_now,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        self.settings = settings
        self.recorder = recorder
        self.redactor = recorder.redactor
        # Also protect callers that constructed a plain Redactor for the recorder.
        self.redactor.add_settings(settings)
        self._timeout = timeout
        self._clock = clock
        self._monotonic = monotonic
        self._token = ""
        self._token_deadline = 0.0
        self._auth_info: AuthInfo | None = None
        self._auth_lock = asyncio.Lock()
        self._http = httpx.AsyncClient(
            base_url=SANDBOX_BASE_URL,
            transport=transport,
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.close()

    async def close(self):
        await self._http.aclose()

    async def authenticate(self) -> AuthInfo:
        """Fetch/cache a token, returning scope and lifetime rather than credentials."""
        async with self._auth_lock:
            if self._auth_info is not None and self._monotonic() < self._token_deadline:
                return self._auth_info
            request = self._http.build_request(
                "POST",
                "/v1/oauth2/token",
                data={"grant_type": "client_credentials"},
                headers={"Accept": "application/json"},
            )
            basic = base64.b64encode(
                f"{self.settings.client_id}:{self.settings.client_secret}".encode()
            ).decode("ascii")
            request.headers["Authorization"] = f"Basic {basic}"
            response = await self._exchange(request, step="oauth_token")
            if response.status_code != 200:
                raise SandboxAuthError(
                    f"Sandbox authentication failed (HTTP {response.status_code})."
                )
            try:
                payload = response.json()
            except ValueError:
                raise SandboxAuthError("Sandbox authentication returned invalid metadata.") from None
            if not isinstance(payload, dict):
                raise SandboxAuthError("Sandbox authentication returned invalid metadata.")
            token = payload.get("access_token")
            expires_in = payload.get("expires_in")
            if (
                not isinstance(token, str)
                or not token.strip()
                or type(expires_in) is not int
                or expires_in <= 0
            ):
                raise SandboxAuthError("Sandbox authentication returned invalid metadata.")
            self.redactor.add_secret(token)
            self._token = token
            self._token_deadline = self._monotonic() + expires_in - min(30, expires_in // 10)
            self._auth_info = AuthInfo(
                scope=self.redactor.text(str(payload.get("scope", ""))), expires_in=expires_in
            )
            return self._auth_info

    async def request(
        self,
        method: str,
        path: str,
        *,
        step: str,
        request_id: str | None = None,
        timeout: float | httpx.Timeout | None = None,
        headers: Mapping[str, str] | None = None,
        params=None,
        json=None,
        data=None,
        files=None,
    ) -> httpx.Response:
        """Call a relative sandbox path once, recording success or ambiguity.

        Supports httpx json/data/files payloads and a per-request timeout.
        HTTP error responses are returned for the check to classify.
        """
        parsed = urlsplit(path)
        if (
            not path.startswith("/")
            or path.startswith("//")
            or parsed.scheme
            or parsed.netloc
            or parsed.fragment
        ):
            raise ValueError("Use a relative sandbox API path beginning with a single slash.")
        await self.authenticate()
        request_headers = httpx.Headers(headers or {})
        request_headers["Authorization"] = f"Bearer {self._token}"
        if request_id is not None:
            request_headers["PayPal-Request-Id"] = request_id
        request = self._http.build_request(
            method,
            path,
            headers=request_headers,
            params=params,
            json=json,
            data=data,
            files=files,
            timeout=self._timeout if timeout is None else timeout,
        )
        return await self._exchange(request, step=step)

    async def _exchange(self, request: httpx.Request, *, step: str) -> httpx.Response:
        request_body = body_value(request.read(), request.headers.get("content-type", ""))
        sent = self._clock()
        try:
            response = await self._http.send(request)
        except httpx.HTTPError as error:
            self.recorder.record(
                step=step,
                method=request.method,
                path=request.url.raw_path.decode("ascii"),
                request_headers=dict(request.headers),
                request_body=request_body,
                t_sent=sent,
                t_recv=self._clock(),
                error=type(error).__name__,
            )
            raise SandboxRequestError(
                f"Sandbox exchange failed ({type(error).__name__}); inspect redacted recordings."
            ) from None
        self.recorder.record(
            step=step,
            method=request.method,
            path=request.url.raw_path.decode("ascii"),
            request_headers=dict(request.headers),
            request_body=request_body,
            response_status=response.status_code,
            response_headers=dict(response.headers),
            response_body=body_value(response.content, response.headers.get("content-type", "")),
            t_sent=sent,
            t_recv=self._clock(),
        )
        return response
