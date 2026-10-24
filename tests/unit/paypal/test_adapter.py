"""Only synthetic credentials, HTTP stubs and exact decimal-string money."""

import asyncio
import copy
import json

import httpx
import pytest

from countersign.config import PayPalBehavior, PayPalSettings
from countersign.money import Money
from countersign.paypal.http import (
    Ambiguous,
    DefiniteError,
    Ok,
    PayPalAuthError,
    PayPalDataError,
    PayPalHTTP,
    PayPalTransportError,
    UnsafePayPalURL,
    canonical_payload_hash,
)
from countersign.paypal.read import PayPalRead
from countersign.paypal.sandbox_controls import SandboxControls
from countersign.paypal.webhook_verify import Verification, WebhookVerifier
from countersign.paypal.write import InvalidOperation, PayPalWrite

ID = "019a12de-7598-7000-8000-000000000001"
KEY = f"cs-{ID}-a1"
TOKEN = "FAKE_BEARER_ONLY_IN_STUB"


def operation(kind="PAYOUT_CREATE", *, state="IN_FLIGHT", body=None):
    default = {
        "sender_batch_header": {"sender_batch_id": KEY},
        "items": [
            {
                "recipient_type": "EMAIL",
                "receiver": "synthetic-maker@example.test",
                "amount": {"value": "1.00", "currency": "USD"},
                "sender_item_id": KEY + "-i1",
            }
        ],
    }
    payload = {
        "body": default if body is None else body,
        "request_id": KEY,
        "payout_item_id": "ITEM-1",
        "paypal_dispute_id": "PP-D-1",
    }
    return {
        "id": ID,
        "kind": kind,
        "state": state,
        "payload": payload,
        "payload_hash": canonical_payload_hash(payload),
    }


def stub(handler):
    def serve(request):
        if request.url.path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": TOKEN, "expires_in": 3600})
        return handler(request)

    return httpx.MockTransport(serve)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (201, {"batch_header": {"payout_batch_id": "BATCH-1"}}, Ok),
        (204, None, Ok),
        (400, {"name": "INSUFFICIENT_FUNDS"}, DefiniteError),
        (401, {"name": "INVALID_TOKEN"}, DefiniteError),
        (403, {"name": "AUTHORIZATION_ERROR"}, DefiniteError),
        (422, {"name": "INVALID_REQUEST"}, DefiniteError),
        (500, {"name": "INTERNAL_ERROR"}, Ambiguous),
        (503, {"name": "UNAVAILABLE"}, Ambiguous),
        (302, {"name": "REDIRECT"}, Ambiguous),
        (400, {"name": "PREVIOUS_REQUEST_IN_PROGRESS"}, Ambiguous),
        (409, {"name": "DUPLICATE_SENDER_BATCH_ID"}, Ambiguous),
        (400, {"name": "DUPLICATE_BATCH_ID"}, Ambiguous),
        (400, {"details": [{"issue": "PREVIOUS_REQUEST_IN_PROGRESS"}]}, Ambiguous),
        (400, {"name": "REQUEST_TIMEOUT_EXCEEDED"}, Ambiguous),
        (
            400,
            {
                "name": "USER_BUSINESS_ERROR",
                "links": [
                    {
                        "rel": "original_batch",
                        "href": "http://127.0.0.1:8099/v1/payments/payouts/BATCH-1",
                    }
                ],
            },
            Ambiguous,
        ),
        (400, {"name": "USER_BUSINESS_ERROR"}, DefiniteError),
    ],
)
async def test_write_classifications_once(status, body, expected):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, json=body) if body is not None else httpx.Response(status)

    async with PayPalHTTP(PayPalSettings(), transport=stub(handler)) as http:
        result = await PayPalWrite(http).create_payout_single_item(operation())
    assert isinstance(result, expected)
    assert len(calls) == 1
    assert calls[0].headers["PayPal-Request-Id"] == KEY
    if isinstance(result, Ambiguous):
        assert result.request_id == KEY
        if body and body.get("links"):
            assert result.original_batch_link.endswith("/BATCH-1")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error", [httpx.ReadTimeout, httpx.ConnectError, httpx.RemoteProtocolError]
)
async def test_network_errors_never_retry_or_print_private_exception(error, capsys, caplog):
    calls = []

    def handler(request):
        calls.append(request)
        raise error(TOKEN + " synthetic-maker@example.test", request=request)

    async with PayPalHTTP(PayPalSettings(), transport=stub(handler)) as http:
        result = await PayPalWrite(http).create_payout_single_item(operation())
    assert isinstance(result, Ambiguous)
    assert result.reason == "PayPalTransportError"
    assert len(calls) == 1
    assert TOKEN not in repr(result) + capsys.readouterr().out + caplog.text
    assert "synthetic-maker@example.test" not in repr(result) + caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit", [True, False])
@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 422, 429])
async def test_any_4xx_on_unknown_replay_cannot_fail_original(status, explicit):
    async with PayPalHTTP(
        PayPalSettings(),
        transport=stub(lambda _: httpx.Response(status, json={"name": "INVALID_REQUEST"})),
    ) as http:
        row = operation(state="IN_FLIGHT" if explicit else "UNKNOWN")
        result = await PayPalWrite(http).create_payout_single_item(row, replay_unknown=explicit)
    assert isinstance(result, Ambiguous)
    assert result.request_id == KEY


@pytest.mark.asyncio
async def test_unknown_replay_can_confirm_success_and_reuses_exact_canonical_bytes():
    sent = []
    row = operation(state="UNKNOWN")

    def handler(request):
        sent.append(request.content)
        if len(sent) == 1:
            raise httpx.ReadTimeout("lost", request=request)
        return httpx.Response(201, json={"batch_header": {"payout_batch_id": "BATCH-1"}})

    async with PayPalHTTP(PayPalSettings(), transport=stub(handler)) as http:
        writer = PayPalWrite(http)
        assert isinstance(await writer.create_payout_single_item(row), Ambiguous)
        assert isinstance(await writer.create_payout_single_item(row), Ok)
    assert len(sent) == 2 and sent[0] == sent[1]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["body", "missing_hash", "state", "kind", "id", "two_items", "float"]
)
async def test_invalid_or_changed_persisted_operation_never_calls_network(change):
    row = operation()
    if change == "body":
        row["payload"]["body"]["items"][0]["receiver"] = "changed@example.test"
    elif change == "missing_hash":
        row.pop("payload_hash")
    elif change == "state":
        row["state"] = "PENDING"
    elif change == "kind":
        row["kind"] = "PAYOUT_CANCEL"
    elif change == "id":
        row["id"] = "unsaved"
    else:
        if change == "two_items":
            row["payload"]["body"]["items"] *= 2
        else:
            row["payload"]["body"]["items"][0]["amount"]["value"] = 1.5
        row["payload_hash"] = canonical_payload_hash(row["payload"])
    calls = []
    async with PayPalHTTP(
        PayPalSettings(), transport=httpx.MockTransport(lambda r: calls.append(r))
    ) as http:
        with pytest.raises(InvalidOperation):
            await PayPalWrite(http).create_payout_single_item(row)
    assert calls == []


@pytest.mark.asyncio
async def test_binary_database_fingerprint_and_private_audit_sanitization(capsys, caplog):
    settings = PayPalSettings(client_id="SYNTHETIC_CLIENT", client_secret="SYNTHETIC_SECRET")

    def handler(request):
        return httpx.Response(
            400,
            headers={"paypal-debug-id": "abc123"},
            json={
                "name": "INVALID_REQUEST",
                "Authorization": "Bearer " + TOKEN,
                "arbitrary": settings.client_id + settings.client_secret + TOKEN,
                "receiver": "synthetic-maker@example.test",
                "PayPal-Auth-Assertion": "FAKE_ASSERTION",
            },
        )

    async with PayPalHTTP(settings, transport=stub(handler)) as http:
        row = operation()
        row["payload_hash"] = bytes.fromhex(row["payload_hash"][7:])
        result = await PayPalWrite(http).create_payout_single_item(row)
        safe = repr(result) + repr(result.resp.safe_body) + repr(http) + repr(http.redactor)
    safe += capsys.readouterr().out + caplog.text
    for secret in (
        settings.client_id,
        settings.client_secret,
        TOKEN,
        "synthetic-maker@example.test",
        "FAKE_ASSERTION",
    ):
        assert secret not in safe
    assert result.resp.debug_id == "abc123"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "https://api-m.paypal.com/v1/payments/payouts",
        "https://evil.example/v1/foo",
        "//evil.example/v1/foo",
        "http://127.0.0.1:8098/v1/foo",
        "/v1/../v1/foo",
        "/v1/%2e%2e/v1/foo",
        "/v1/%255cfoo",
        "/v1/foo#fragment",
        "v1/foo",
        "http://user:password@127.0.0.1:8099/v1/foo",
        "/v1/foo\nsecret",
        "/_mock/reset",
    ],
)
async def test_unsafe_urls_fail_before_authentication(path):
    calls = []
    async with PayPalHTTP(
        PayPalSettings(), transport=httpx.MockTransport(lambda r: calls.append(r))
    ) as http:
        with pytest.raises(UnsafePayPalURL):
            await PayPalRead(http).follow(path)
    assert calls == []


@pytest.mark.asyncio
async def test_redirect_not_followed_and_cannot_replace_auth():
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={"Location": "https://api-m.paypal.com/v1/foo"})

    async with PayPalHTTP(PayPalSettings(), transport=stub(handler)) as http:
        assert (await http.request("GET", "/v1/foo")).status_code == 302
        with pytest.raises(PayPalDataError):
            await http.request("GET", "/v1/foo", headers={"Authorization": "changed"})
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_oauth_cached_across_concurrent_reads_and_refreshed_before_expiry():
    now, auths = [0], []

    def handler(request):
        if request.url.path == "/v1/oauth2/token":
            auths.append(request)
            return httpx.Response(200, json={"access_token": TOKEN, "expires_in": 60})
        assert request.headers["authorization"] == "Bearer " + TOKEN
        return httpx.Response(200, json={})

    async with PayPalHTTP(
        PayPalSettings(), transport=httpx.MockTransport(handler), monotonic=lambda: now[0]
    ) as http:
        await asyncio.gather(*(http.request("GET", "/v1/foo") for _ in range(5)))
        assert len(auths) == 1
        now[0] = 31
        await http.request("GET", "/v1/foo")
    assert len(auths) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {},
        [],
        {"access_token": TOKEN, "expires_in": -1},
        {"access_token": TOKEN, "expires_in": True},
    ],
)
async def test_oauth_invalid_is_safe_failure(body):
    async with PayPalHTTP(
        PayPalSettings(), transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body))
    ) as http:
        with pytest.raises(PayPalAuthError, match="PayPal OAuth failed"):
            await http.request("GET", "/v1/foo")


@pytest.mark.asyncio
async def test_read_money_parsed_exactly_raw_status_kept():
    body = {
        "transaction_status": "UNRECOGNIZED_FUTURE_STATUS",
        "amount": {"value": "12.34", "currency": "USD"},
    }
    async with PayPalHTTP(
        PayPalSettings(), transport=stub(lambda _: httpx.Response(200, json=body))
    ) as http:
        result = await PayPalRead(http).get_payout_item("ITEM-1")
    assert result.body == body
    assert result.amounts["/amount"] == Money(1234, "USD")


@pytest.mark.asyncio
async def test_malformed_money_fails_without_echoing_value():
    async with PayPalHTTP(
        PayPalSettings(),
        transport=stub(
            lambda _: httpx.Response(
                200,
                json={
                    "amount": {"value": "SYNTHETIC_SECRET", "currency": "USD"},
                },
            )
        ),
    ) as http:
        with pytest.raises(PayPalDataError, match="^Malformed PayPal money amount$"):
            await PayPalRead(http).get_capture("CAPTURE-1")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "kind", "suffix", "format"),
    [
        ("cancel_item", "PAYOUT_CANCEL", "/cancel", "none"),
        ("provide_evidence", "DISPUTE_EVIDENCE", "/provide-evidence", "multipart"),
        ("accept_claim", "DISPUTE_ACCEPT_CLAIM", "/accept-claim", "json"),
    ],
)
async def test_other_writes_use_persisted_target_and_configured_format_once(
    method, kind, suffix, format
):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(204)

    async with PayPalHTTP(PayPalSettings(), transport=stub(handler)) as http:
        result = await getattr(PayPalWrite(http), method)(operation(kind, body={"note": "test"}))
    assert isinstance(result, Ok)
    assert len(calls) == 1 and calls[0].url.path.endswith(suffix)
    if format == "none":
        assert calls[0].content == b""
    elif format == "multipart":
        assert calls[0].headers["content-type"].startswith("multipart/form-data;")
        assert b'name="input"' in calls[0].content
    else:
        assert json.loads(calls[0].content) == {"note": "test"}


@pytest.mark.asyncio
async def test_pending_format_defaults_can_be_changed_without_hidden_fallback():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(415, json={"name": "UNSUPPORTED_MEDIA_TYPE"})

    settings = PayPalSettings(
        behavior=PayPalBehavior(evidence_format="json", accept_claim_format="multipart")
    )
    async with PayPalHTTP(settings, transport=stub(handler)) as http:
        writer = PayPalWrite(http)
        assert isinstance(
            await writer.provide_evidence(operation("DISPUTE_EVIDENCE", body={})), DefiniteError
        )
        assert isinstance(
            await writer.accept_claim(operation("DISPUTE_ACCEPT_CLAIM", body={})), DefiniteError
        )
    assert len(calls) == 2
    assert calls[0].headers["content-type"] == "application/json"
    assert calls[1].headers["content-type"].startswith("multipart/form-data;")


HEADERS = {
    "PayPal-Auth-Algo": "SHA256withRSA",
    "PayPal-Cert-Url": "https://unfetched.invalid/cert",
    "PayPal-Transmission-Id": "TRANS-1",
    "PayPal-Transmission-Sig": "FAKE_SIGNATURE",
    "PayPal-Transmission-Time": "2026-10-24T00:00:00Z",
}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (200, {"verification_status": "SUCCESS"}, Verification.SUCCESS),
        (200, {"verification_status": "FAILURE"}, Verification.FAILURE),
        (200, {}, Verification.UNKNOWN),
        (200, [], Verification.UNKNOWN),
        (200, {"verification_status": []}, Verification.UNKNOWN),
        (200, {"verification_status": "success"}, Verification.UNKNOWN),
        (500, {"verification_status": "FAILURE"}, Verification.UNKNOWN),
        (400, {"verification_status": "FAILURE"}, Verification.UNKNOWN),
    ],
)
async def test_signature_only_explicit_success_or_failure_on_successful_postback(
    status, body, expected
):
    calls = []
    raw = b' { "id" : "WH-1", "resource" : {"x": 1} }\n'
    before_headers = copy.deepcopy(HEADERS)

    def handler(request):
        calls.append(request)
        postback = json.loads(request.content)
        assert postback["webhook_event"] == json.loads(raw)
        assert postback["transmission_sig"] == "FAKE_SIGNATURE"
        return httpx.Response(status, json=body)

    async with PayPalHTTP(
        PayPalSettings(webhook_id="FAKE_WEBHOOK_ID"), transport=stub(handler)
    ) as http:
        assert await WebhookVerifier(http).verify(raw, HEADERS) == expected
    assert HEADERS == before_headers and raw.endswith(b"\n")
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_verification_network_error_and_malformed_raw_stay_unknown():
    def handler(request):
        raise httpx.ReadTimeout(TOKEN, request=request)

    async with PayPalHTTP(
        PayPalSettings(webhook_id="FAKE_WEBHOOK_ID"), transport=stub(handler)
    ) as http:
        verifier = WebhookVerifier(http)
        for raw, headers in [
            (b'{"id":"WH-1"}', HEADERS),
            (b"not json", HEADERS),
            (b'{"id":"a","id":"b"}', HEADERS),
            (b"{}", {}),
        ]:
            assert await verifier.verify(raw, headers) == Verification.UNKNOWN


@pytest.mark.asyncio
async def test_sandbox_controls_call_only_named_staged_endpoints():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"status": "UNDER_REVIEW"})

    async with PayPalHTTP(PayPalSettings(), transport=stub(handler)) as http:
        controls = SandboxControls(http)
        await controls.require_evidence("PP-D-1", {"action": "SELLER_EVIDENCE"})
        await controls.adjudicate("PP-D-1", {"outcome": "SELLER_FAVOR"})
    assert [request.url.path.rsplit("/", 1)[-1] for request in calls] == [
        "require-evidence",
        "adjudicate",
    ]


def test_shared_fingerprint_is_stable_and_rejects_non_json():
    assert canonical_payload_hash({"b": "é", "a": 1}) == canonical_payload_hash({"a": 1, "b": "é"})
    with pytest.raises(PayPalDataError):
        canonical_payload_hash({"bad": float("nan")})


@pytest.mark.asyncio
async def test_transport_exception_from_read_carries_only_error_type():
    def handler(request):
        raise httpx.ConnectError(TOKEN, request=request)

    async with PayPalHTTP(PayPalSettings(), transport=stub(handler)) as http:
        with pytest.raises(PayPalTransportError, match="^ConnectError$"):
            await PayPalRead(http).get_dispute("PP-D-1")
