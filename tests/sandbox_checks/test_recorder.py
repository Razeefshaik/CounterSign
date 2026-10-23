import asyncio
import base64
import json
from datetime import UTC, datetime

import httpx
import pytest

from scripts.sandbox_checks.client import SandboxAuthError, SandboxClient, SandboxRequestError
from scripts.sandbox_checks.env import SandboxSettings
from scripts.sandbox_checks.recorder import Recorder
from scripts.sandbox_checks.redact import Redactor

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)


@pytest.fixture
def settings():
    return SandboxSettings(
        client_id="FAKE-CLIENT-ID",
        client_secret="FAKE-CLIENT-SECRET",
        maker_a_email="maker-a@fake.example",
    )


@pytest.fixture
def recorder(tmp_path, settings):
    return Recorder(
        "D1", root=tmp_path, redactor=Redactor.from_settings(settings), clock=lambda: NOW
    )


def recordings(recorder):
    return sorted((recorder.root / "2026-10-23" / recorder.check_id).glob("*.json"))


def test_recorder_layout_fields_redaction_and_resumed_sequence(recorder, settings):
    first = recorder.record(
        step="auth",
        method="post",
        path="/v1/oauth2/token",
        request_headers={"Authorization": "Basic FAKE-BASIC"},
        request_body={"client_secret": settings.client_secret},
        response_status=200,
        response_headers={"paypal-debug-id": "debug-id-1"},
        response_body={"access_token": "FAKE-TOKEN", "email": settings.maker_a_email},
    )
    restarted = Recorder("D1", root=recorder.root, redactor=recorder.redactor, clock=lambda: NOW)
    second = restarted.record(step="scopes", method="GET", path="/v1/reporting/balances")

    assert first.relative_to(recorder.root).as_posix() == "2026-10-23/D1/01_auth.json"
    assert second.name == "02_scopes.json"
    saved = json.loads(first.read_text(encoding="utf-8"))
    assert saved["method"] == "POST"
    assert saved["response_status"] == 200
    assert saved["response_headers"]["paypal-debug-id"] == "debug-id-1"
    assert saved["t_sent"] == saved["t_recv"] == "2026-10-23T12:00:00Z"
    assert "Authorization" not in saved["request_headers"]
    public = first.read_text(encoding="utf-8")
    assert "FAKE-TOKEN" not in public
    assert settings.client_secret not in public
    assert settings.maker_a_email not in public
    assert saved["response_body"]["email"] == "maker-a@redacted"
    assert not (recorder.root / "raw").exists()


def test_raw_retention_is_explicit_and_stays_under_raw_directory(recorder):
    recorder.retain_raw = True
    public = recorder.record(
        step="raw",
        method="POST",
        path="/v1/example",
        request_headers={
            "Authorization": "Bearer FAKE-RAW-TOKEN",
            "Proxy-Authorization": "Basic FAKE-PROXY",
        },
        request_body=b"FAKE-ORIGINAL-BYTES",
        response_headers={"AUTHORIZATION": "Bearer FAKE-RESPONSE-TOKEN", "paypal-debug-id": "debug"},
    )
    raw_file = recorder.root / "raw" / "2026-10-23" / "D1" / public.name
    raw = json.loads(raw_file.read_text(encoding="utf-8"))

    assert raw["request_headers"] == {}
    assert raw["response_headers"] == {"paypal-debug-id": "debug"}
    assert "FAKE-RAW-TOKEN" not in raw_file.read_text(encoding="utf-8")
    assert "FAKE-PROXY" not in raw_file.read_text(encoding="utf-8")
    assert "FAKE-RESPONSE-TOKEN" not in raw_file.read_text(encoding="utf-8")
    assert base64.b64decode(raw["request_body"]["data"]) == b"FAKE-ORIGINAL-BYTES"
    assert "FAKE-RAW-TOKEN" not in public.read_text(encoding="utf-8")


def test_step_cannot_escape_directory_or_include_known_secrets(recorder, settings):
    destination = recorder.record(
        step=f"../../{settings.client_secret}/step", method="GET", path="/v1/example"
    )
    assert destination.parent == recorder.root / "2026-10-23" / "D1"
    assert settings.client_secret not in destination.name


@pytest.mark.parametrize("check_id", ("../private", "D14", "D1/../../private"))
def test_check_id_cannot_escape_recordings_directory(tmp_path, check_id):
    with pytest.raises(ValueError, match="Check ID"):
        Recorder(check_id, root=tmp_path, redactor=Redactor())


@pytest.mark.asyncio
async def test_client_token_cache_refresh_request_ids_and_timeouts(recorder, settings):
    requests = []
    token_count = 0
    monotonic = [0.0]

    def handle(request):
        nonlocal token_count
        requests.append(request)
        if request.url.path == "/v1/oauth2/token":
            token_count += 1
            assert request.headers["Authorization"].startswith("Basic ")
            assert request.content == b"grant_type=client_credentials"
            return httpx.Response(
                200,
                json={"access_token": f"FAKE-TOKEN-{token_count}", "expires_in": 100, "scope": "payouts"},
            )
        assert request.url.host == "api-m.sandbox.paypal.com"
        assert request.headers["Authorization"] == f"Bearer FAKE-TOKEN-{token_count}"
        return httpx.Response(200, json={"ok": True}, headers={"paypal-debug-id": "debug-test"})

    async with SandboxClient(
        settings, recorder, transport=httpx.MockTransport(handle),
        clock=lambda: NOW, monotonic=lambda: monotonic[0],
    ) as client:
        await client.request("GET", "/v1/example", step="first", request_id="cs-fake-a1", timeout=0.05)
        await client.request("GET", "/v1/example", step="second")
        assert token_count == 1
        monotonic[0] = 101.0
        metadata = await client.authenticate()
        await client.request("GET", "/v1/example", step="third")

    assert token_count == 2
    assert metadata.scope == "payouts"
    assert requests[1].headers["PayPal-Request-Id"] == "cs-fake-a1"
    assert requests[1].extensions["timeout"]["read"] == 0.05
    assert requests[2].extensions["timeout"]["read"] == 30.0
    assert len(recordings(recorder)) == 5
    public = "".join(path.read_text(encoding="utf-8") for path in recordings(recorder))
    for private in (settings.client_id, settings.client_secret, "FAKE-TOKEN-1", "FAKE-TOKEN-2"):
        assert private not in public
    assert json.loads(recordings(recorder)[1].read_text())["response_headers"]["paypal-debug-id"] == "debug-test"


@pytest.mark.asyncio
async def test_concurrent_requests_share_one_token_request(recorder, settings):
    tokens = []

    async def handle(request):
        if request.url.path == "/v1/oauth2/token":
            tokens.append(request)
            await asyncio.sleep(0)
            return httpx.Response(200, json={"access_token": "FAKE-TOKEN", "expires_in": 100})
        return httpx.Response(200, json={"ok": True})

    async with SandboxClient(
        settings, recorder, transport=httpx.MockTransport(handle), clock=lambda: NOW
    ) as client:
        await asyncio.gather(
            *(client.request("GET", "/v1/example", step=f"parallel_{index}") for index in range(3))
        )

    assert len(tokens) == 1
    assert len(recordings(recorder)) == 4


@pytest.mark.asyncio
async def test_multipart_and_http_error_response_are_recorded(recorder, settings):
    def handle(request):
        if request.url.path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "FAKE-TOKEN", "expires_in": 100})
        assert request.headers["Content-Type"].startswith("multipart/form-data; boundary=")
        assert b'filename="fake.txt"' in request.content
        assert b"fake evidence" in request.content
        return httpx.Response(422, json={"name": "FAKE_ERROR", "receiver": settings.maker_a_email})

    async with SandboxClient(
        settings, recorder, transport=httpx.MockTransport(handle), clock=lambda: NOW
    ) as client:
        response = await client.request(
            "POST", "/v1/customer/disputes/FAKE-ID/provide-evidence", step="evidence",
            data={"input": json.dumps({"email": settings.maker_a_email})},
            files={"evidence": ("fake.txt", b"fake evidence", "text/plain")},
        )

    assert response.status_code == 422
    saved = json.loads(recordings(recorder)[1].read_text(encoding="utf-8"))
    assert saved["response_status"] == 422
    assert saved["response_body"]["receiver"] == "maker-a@redacted"
    assert settings.maker_a_email not in json.dumps(saved)


@pytest.mark.asyncio
async def test_transport_error_is_recorded_once_without_leaking_exception_text(recorder, settings):
    calls = []

    def handle(request):
        if request.url.path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "FAKE-TOKEN", "expires_in": 100})
        calls.append(request)
        raise httpx.ReadTimeout(f"{settings.client_secret} {settings.maker_a_email}", request=request)

    async with SandboxClient(
        settings, recorder, transport=httpx.MockTransport(handle), clock=lambda: NOW
    ) as client:
        with pytest.raises(SandboxRequestError) as error:
            await client.request("POST", "/v1/payments/payouts", step="lost_response")

    assert len(calls) == 1
    assert settings.client_secret not in str(error.value)
    assert settings.maker_a_email not in str(error.value)
    saved = json.loads(recordings(recorder)[1].read_text())
    assert saved["response_status"] is None
    assert saved["error"] == "ReadTimeout"


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", ({"access_token": "FAKE-TOKEN"}, [], {"expires_in": 100}))
async def test_invalid_token_metadata_fails_safely(recorder, settings, payload):
    async with SandboxClient(
        settings, recorder,
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload)),
        clock=lambda: NOW,
    ) as client:
        with pytest.raises(SandboxAuthError, match="invalid metadata"):
            await client.authenticate()


@pytest.mark.asyncio
async def test_authentication_error_does_not_echo_response_secrets(recorder, settings):
    async with SandboxClient(
        settings, recorder,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(401, json={"error": settings.client_secret})
        ),
        clock=lambda: NOW,
    ) as client:
        with pytest.raises(SandboxAuthError) as error:
            await client.authenticate()

    assert "HTTP 401" in str(error.value)
    assert settings.client_secret not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ("https://untrusted.example", "//untrusted.example", "///example"))
async def test_client_rejects_other_origins_before_authentication(recorder, settings, path):
    calls = []
    async with SandboxClient(
        settings, recorder, transport=httpx.MockTransport(lambda request: calls.append(request))
    ) as client:
        with pytest.raises(ValueError, match="relative sandbox"):
            await client.request("GET", path, step="bad_path")
    assert calls == []
