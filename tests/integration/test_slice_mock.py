"""C21 flow through public facade calls; test SQL is observation/setup only."""

import json
from datetime import UTC, datetime

import httpx
import pytest

from scripts.sandbox_checks.client import SandboxClient
from scripts.sandbox_checks.env import SandboxSettings
from scripts.sandbox_checks.manual import ManualSteps
from scripts.sandbox_checks.redact import Redactor
from scripts.slice_e2e import (
    Checkpoint,
    ExchangeRecorder,
    LostResponse,
    SliceError,
    sandbox_orders,
)

NOW = datetime(2026, 11, 6, 9, tzinfo=UTC)


def checkpoint(path, **options):
    return Checkpoint(
        path,
        target="sandbox",
        identity={"run": "fake"},
        redactor=Redactor(secrets=("FAKE-PRIVATE",)),
        clock=lambda: NOW,
        output=lambda _: None,
        **options,
    )


@pytest.mark.asyncio
async def test_checkpoint_resumes_completed_step_and_redacts_public_files(tmp_path):
    store = checkpoint(tmp_path)
    calls = []

    async def write():
        calls.append(1)
        assert json.loads(store.path.read_text())["steps"]["write"] == "IN_PROGRESS"
        store.checkpoint(
            capture_id="CAPTURE-123", private="FAKE-PRIVATE", email="person@example.test"
        )
        store.record("RESOURCE", capture_id="CAPTURE-123", access_token="FAKE-PRIVATE")

    await store.step("write", write)
    store.close()
    resumed = checkpoint(tmp_path)
    await resumed.step("write", write)
    resumed.close()
    assert calls == [1]
    public = (tmp_path / "state.json").read_text() + (tmp_path / "run.jsonl").read_text()
    assert "CAPTURE-123" in public
    assert "FAKE-PRIVATE" not in public and "person@example.test" not in public


def test_checkpoint_refuses_concurrent_run_and_changed_identity(tmp_path):
    store = checkpoint(tmp_path)
    with pytest.raises(SliceError, match="SLICE_ALREADY_RUNNING"):
        checkpoint(tmp_path)
    store.close()
    with pytest.raises(SliceError, match="SAVED_RUN_SETTINGS_CHANGED"):
        Checkpoint(tmp_path, target="sandbox", identity={"run": "other"}, redactor=Redactor())


@pytest.mark.asyncio
async def test_checkpoint_never_marks_interrupted_step_succeeded(tmp_path):
    store = checkpoint(tmp_path)

    async def interrupted():
        raise OSError("fake interruption")

    with pytest.raises(OSError):
        await store.step("capture", interrupted)
    assert json.loads(store.path.read_text())["steps"]["capture"] == "IN_PROGRESS"
    store.close()


class UnreadResponse(httpx.AsyncByteStream):
    async def __aiter__(self):
        pytest.fail("the staged transport must discard the response before reading")
        yield b""


@pytest.mark.asyncio
async def test_lost_response_transport_is_write_ahead_unread_and_not_rearmed(tmp_path):
    store = checkpoint(tmp_path)
    requests = []

    async def respond(request):
        requests.append(request)
        assert json.loads(store.path.read_text())["data"]["lost_response_armed"] is True
        return httpx.Response(201, stream=UnreadResponse())

    wrapper = LostResponse(httpx.MockTransport(respond), store)
    request = httpx.Request("POST", "https://api-m.sandbox.paypal.com/v1/payments/payouts")
    with pytest.raises(httpx.ReadTimeout):
        await wrapper.handle_async_request(request)
    store.close()
    resumed = checkpoint(tmp_path)
    second = LostResponse(httpx.MockTransport(respond), resumed)
    response = await second.handle_async_request(request)
    await response.aclose()
    assert len(requests) == 2  # only the executor may decide to replay identical keys
    assert (tmp_path / "run.jsonl").read_text().count('"STAGED_LOST_RESPONSE"') == 1
    resumed.close()


@pytest.mark.asyncio
async def test_sandbox_order_helper_creates_and_captures_two_orders_once(tmp_path):
    store = checkpoint(tmp_path)
    settings = SandboxSettings("FAKE-CLIENT", "FAKE-PRIVATE")
    seen, captured = [], set()

    def respond(request):
        seen.append((request.method, request.url.path))
        if request.url.path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "FAKE-PRIVATE", "expires_in": 300})
        if request.url.path == "/v2/checkout/orders":
            maker = json.loads(request.content)["purchase_units"][0]["reference_id"][-1]
            return httpx.Response(
                201,
                json={
                    "id": "ORDER-" + maker,
                    "links": [{"rel": "approve", "href": "https://www.sandbox.paypal.com/approve"}],
                },
            )
        maker = request.url.path.split("/")[4][-1]
        if request.method == "POST":
            captured.add(maker)
        body = {"id": "ORDER-" + maker, "status": "COMPLETED" if maker in captured else "APPROVED"}
        if maker in captured:
            body["purchase_units"] = [
                {"payments": {"captures": [{"id": "CAPTURE-" + maker, "status": "COMPLETED"}]}}
            ]
        return httpx.Response(200, json=body)

    manual = ManualSteps(store.redactor, input_fn=lambda _: "", output_fn=lambda _: None)
    async with SandboxClient(
        settings, ExchangeRecorder(store), transport=httpx.MockTransport(respond)
    ) as client:
        orders = await sandbox_orders(store, client, manual)
        await sandbox_orders(store, client, manual)
    assert {v["capture_id"] for v in orders.values()} == {"CAPTURE-A", "CAPTURE-B"}
    assert sum(method == "POST" and path == "/v2/checkout/orders" for method, path in seen) == 2
    assert sum(method == "POST" and path.endswith("/capture") for method, path in seen) == 2
    assert "FAKE-PRIVATE" not in (tmp_path / "run.jsonl").read_text()
    store.close()
