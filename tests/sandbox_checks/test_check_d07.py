import json
import re
import uuid
from datetime import UTC, datetime

import httpx
import pytest

from scripts.sandbox_checks.checks import d07_negative as d07
from scripts.sandbox_checks.env import SandboxSettings
from scripts.sandbox_checks.manual import ManualSteps
from scripts.sandbox_checks.redact import Redactor
from scripts.sandbox_checks.runner import CheckFailed, Runner

NOW = datetime(2026, 10, 23, 9, 0, tzinfo=UTC)
CLIENT_ID = "FAKE-CLIENT-ID"
SECRET = "FAKE-CLIENT-SECRET"
TOKEN = "FAKE-ACCESS-TOKEN"
MAKER_B = "maker-b@fake.example"
KEY = re.compile(r"cs-[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}-a1")
# Documented names, with the HTTP statuses the mock currently assumes.
DOCUMENTED = {
    "ERRPYO005": (422, "INSUFFICIENT_FUNDS"),
    "ERRPYO006": (500, "INTERNAL_ERROR"),
    "ERRPYO014": (422, "USER_BUSINESS_ERROR"),
    "ERRPYO036": (504, "REQUEST_TIMEOUT_EXCEEDED"),
    "ERRPYO039": (409, "PREVIOUS_REQUEST_IN_PROGRESS"),
    "ERRPYO007": (400, "ITEM_ALREADY_CANCELLED"),
    "ERRPYO008": (400, "ITEM_CANCELLATION_FAILED"),
    "ERRPYO009": (400, "ITEM_INCORRECT_STATUS"),
    "ERRPYO015": (200, "CLOSED_MARKET"),
    "ERRPYO047": (200, "RECEIVER_UNREGISTERED"),
    "ERRPYO052": (200, "RISK_DECLINE"),
    "ERRPYOB002": (200, "PENDING"),
    "ERRPYOB003": (200, "PROCESSING"),
    "ERRPYOB004": (200, "DENIED"),
}
CREATES = ("ERRPYO005", "ERRPYO006", "ERRPYO014", "ERRPYO036", "ERRPYO039")


def envelope(status, name, value):
    return httpx.Response(
        status,
        json={
            "name": name,
            "message": "Simulated",
            "debug_id": f"dbg-{value.lower()}",
            "information_link": "https://developer.paypal.com/api/rest/responses/",
        },
    )


class FakePayPal:
    """Answers the documented Payouts test values, with switches for each failure mode."""

    def __init__(self, *, overrides=None, succeed=(), drop=(), crash=(), generic=False):
        self.overrides = overrides or {}  # value -> (status, name)
        self.succeed = set(succeed)  # creates that are not simulated: real 201
        self.drop = set(drop)  # creates whose response is lost
        self.crash = set(crash)  # the process dies mid-request, once
        self.generic = generic  # every call returns the same error
        self.requests: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path == "/v1/oauth2/token":
            return httpx.Response(
                200, json={"access_token": TOKEN, "expires_in": 32400, "scope": "x"}
            )
        if path == "/v1/reporting/balances":
            return httpx.Response(200, json={"balances": []})
        if self.generic:
            return envelope(403, "NOT_AUTHORIZED", "generic")
        if path == "/v1/payments/payouts":
            return self.create(request)
        if match := re.fullmatch(r"/v1/payments/payouts/(\w+)", path):
            return self.get_batch(match.group(1))
        if match := re.fullmatch(r"/v1/payments/payouts-item/(\w+)", path):
            return self.get_item(match.group(1))
        if match := re.fullmatch(r"/v1/payments/payouts-item/(\w+)/cancel", path):
            return self.answer(match.group(1))
        return envelope(404, "RESOURCE_NOT_FOUND", "none")

    def answer(self, value):
        status, name = self.overrides.get(value) or DOCUMENTED.get(
            value, (404, "INVALID_RESOURCE_ID")
        )
        return envelope(status, name, value)

    def create(self, request):
        body = json.loads(request.content)
        value = body["items"][0]["note"]
        if value in self.crash:
            self.crash.discard(value)
            raise RuntimeError("process killed")
        if value in self.drop:
            raise httpx.ReadTimeout("lost", request=request)
        if value in self.succeed:
            return httpx.Response(
                201,
                json={
                    "batch_header": {
                        "payout_batch_id": f"BATCH-{value}",
                        "batch_status": "PENDING",
                        "sender_batch_header": body["sender_batch_header"],
                    },
                    "items": [{"payout_item": body["items"][0]}],
                },
            )
        return self.answer(value)

    def get_batch(self, value):
        status, name = self.overrides.get(value) or DOCUMENTED.get(value, (404, None))
        if value == "ERRPYO015":  # the docs example puts CLOSED_MARKET on the batch path
            item = {"transaction_status": "FAILED", "errors": {"name": name}}
            body = {"batch_header": {"batch_status": "SUCCESS"}, "items": [item]}
        elif value.startswith("ERRPYOB"):
            body = {"batch_header": {"payout_batch_id": value, "batch_status": name}}
        else:
            return envelope(404, "INVALID_RESOURCE_ID", value)
        return httpx.Response(status, json=body, headers={"paypal-debug-id": f"hdr-{value}"})

    def get_item(self, value):
        if value == "ERRPYO015":  # not an item value in the docs
            return envelope(404, "INVALID_RESOURCE_ID", value)
        status, name = self.overrides.get(value) or DOCUMENTED[value]
        body = {
            "payout_item_id": value,
            "transaction_status": "FAILED",
            "errors": {"name": name, "message": "Simulated"},
            "payout_item": {"receiver": MAKER_B, "note": value},
        }
        return httpx.Response(status, json=body, headers={"paypal-debug-id": f"hdr-{value}"})

    def creates(self, value=None):
        found = [
            r for r in self.requests if r.method == "POST" and r.url.path == "/v1/payments/payouts"
        ]
        return [
            r for r in found if value is None or json.loads(r.content)["items"][0]["note"] == value
        ]


class Output:
    def __init__(self):
        self.lines: list[str] = []

    def __call__(self, line):
        self.lines.append(line)


@pytest.fixture
def settings():
    return SandboxSettings(client_id=CLIENT_ID, client_secret=SECRET, maker_b_email=MAKER_B)


@pytest.fixture
def root(tmp_path):
    path = tmp_path / "2026-10-23" / "D1" / "state.json"
    path.parent.mkdir(parents=True)
    state = {"schema_version": 1, "check_id": "D1", "completed_steps": [], "data": {}}
    state["data"]["verdict"] = "CONFIRMED"
    path.write_text(json.dumps(state), encoding="utf-8")
    return tmp_path


async def run(settings, root, fake, output):
    runner = Runner(
        settings,
        root=root,
        transport=httpx.MockTransport(fake.handle),
        clock=lambda: NOW,
        output=output,
        manual=ManualSteps(
            Redactor.from_settings(settings), input_fn=lambda _: "", output_fn=output
        ),
    )
    return await runner.run("D7")


def findings(root):
    return json.loads((root / "2026-10-23" / "D7" / "findings.json").read_text(encoding="utf-8"))


def assert_no_private_values(root, output):
    text = "".join(p.read_text(encoding="utf-8") for p in root.rglob("*") if p.is_file())
    text += "\n".join(output.lines)
    for private in (CLIENT_ID, SECRET, TOKEN, MAKER_B):
        assert private not in text


def probe(state, operation, value):
    return state["data"]["probes"][f"{operation}:{value}"]


def test_uuid7_follows_rfc_9562():
    value = d07.uuid7(NOW)
    assert value.version == 7
    assert value.variant == uuid.RFC_4122
    assert value.int >> 80 == int(NOW.timestamp() * 1000)
    assert d07.uuid7(NOW) != value


@pytest.mark.asyncio
async def test_every_documented_value_is_confirmed(settings, root):
    fake, output = FakePayPal(), Output()

    state = await run(settings, root, fake, output)

    finding = findings(root)
    assert finding["verdict"] == "CONFIRMED"
    assert {v["value"] for v in finding["values"]} == set(DOCUMENTED)
    assert all(v["matched"] is True for v in finding["values"])
    [closed] = [v for v in finding["values"] if v["value"] == "ERRPYO015"]
    assert closed["matched_on"] == ["get_batch"]
    assert probe(state, "get_item", "ERRPYO015")["error_name"] == "INVALID_RESOURCE_ID"
    for row in finding["probes"]:
        assert {"value", "operation", "expected_name", "status", "error_name", "debug_id"} <= set(
            row
        )
        assert row["debug_id"]
    assert finding["fixtures"]["ERRPYOB004"] == {
        "operation": "get_batch",
        "status": 200,
        "name": "DENIED",
        "mock_status": 200,
    }
    assert finding["fixtures"]["ERRPYO015"]["operation"] == "get_batch"
    # Each create: $1.00 to maker-b, the value in items[0].note, fresh cs-<uuid7>-a1 keys.
    keys = set()
    for value in CREATES:
        [request] = fake.creates(value)
        body = json.loads(request.content)
        key = body["sender_batch_header"]["sender_batch_id"]
        assert KEY.fullmatch(key)
        assert request.headers["PayPal-Request-Id"] == key
        item = body["items"][0]
        assert item["amount"] == {"currency": "USD", "value": "1.00"}
        assert item["receiver"] == MAKER_B
        assert item["sender_item_id"] == f"{key}-i1"
        keys.add(key)
    assert len(keys) == len(CREATES)
    assert probe(state, "create", "ERRPYO005")["request_body"]["items"][0]["receiver"] == (
        "maker-b@redacted"
    )
    cancels = [r for r in fake.requests if r.url.path.endswith("/cancel")]
    assert [r.url.path for r in cancels] == [
        f"/v1/payments/payouts-item/ERRPYO00{n}/cancel" for n in (7, 8, 9)
    ]
    assert not state["data"].get("timeline")
    assert_no_private_values(root, output)


@pytest.mark.asyncio
async def test_a_different_name_is_different(settings, root):
    fake, output = FakePayPal(overrides={"ERRPYO006": (500, "INTERNAL_SERVER_ERROR")}), Output()

    state = await run(settings, root, fake, output)

    finding = findings(root)
    assert finding["verdict"] == "DIFFERENT"
    assert finding["mismatched_values"] == ["ERRPYO006"]
    row = probe(state, "create", "ERRPYO006")
    assert (row["status"], row["error_name"], row["matched"]) == (
        500,
        "INTERNAL_SERVER_ERROR",
        False,
    )
    assert finding["fixtures"]["ERRPYO006"]["name"] == "INTERNAL_SERVER_ERROR"
    assert_no_private_values(root, output)


@pytest.mark.asyncio
async def test_an_unsimulated_create_is_recorded_loudly_and_never_retried(settings, root):
    fake, output = FakePayPal(succeed={"ERRPYO005"}), Output()

    state = await run(settings, root, fake, output)

    row = probe(state, "create", "ERRPYO005")
    assert row["outcome"] == "SUCCEEDED"
    assert row["status"] == 201
    assert row["matched"] is False
    assert row["payout_batch_id"] == "BATCH-ERRPYO005"
    assert len(fake.creates()) == 1  # not retried, and the other creates were skipped
    assert {probe(state, "create", v)["outcome"] for v in CREATES[1:]} == {"SKIPPED"}
    [event] = state["data"]["timeline"]
    assert event["event"] == "PAYOUT_SUBMITTED"
    assert event["resource_id"] == "BATCH-ERRPYO005"
    assert (event["amount_minor"], event["currency"]) == (100, "USD")
    finding = findings(root)
    assert finding["verdict"] == "DIFFERENT"
    assert finding["money_moved"][0]["payout_batch_id"] == "BATCH-ERRPYO005"
    assert any("WARNING" in line and "BATCH-ERRPYO005" in line for line in output.lines)

    await run(settings, root, fake, output)  # already complete: nothing is resent
    assert len(fake.creates()) == 1
    assert_no_private_values(root, output)


@pytest.mark.asyncio
async def test_a_lost_create_response_stays_unknown(settings, root):
    fake, output = FakePayPal(drop={"ERRPYO036"}), Output()

    state = await run(settings, root, fake, output)

    row = probe(state, "create", "ERRPYO036")
    assert row["outcome"] == "UNKNOWN"
    assert row["matched"] is None
    assert row["status"] is None
    assert row["transport_error"] == "SandboxRequestError"
    assert len(fake.creates("ERRPYO036")) == 1
    finding = findings(root)
    assert finding["verdict"] == "BLOCKED"  # missing evidence, not a negative finding
    assert finding["unknown_values"] == ["ERRPYO036"]
    assert finding["unknown_creates"][0]["sender_batch_id"] == row["sender_batch_id"]
    assert_no_private_values(root, output)


@pytest.mark.asyncio
async def test_an_interrupted_create_is_not_resent_on_resume(settings, root):
    fake, output = FakePayPal(crash={"ERRPYO014"}), Output()

    with pytest.raises(CheckFailed):
        await run(settings, root, fake, output)
    assert len(fake.creates("ERRPYO014")) == 1
    assert not fake.creates("ERRPYO036")

    state = await run(settings, root, fake, output)

    row = probe(state, "create", "ERRPYO014")
    assert row["outcome"] == "UNKNOWN"
    assert row["unknown_reason"] == "interrupted; not resent"
    assert len(fake.creates("ERRPYO014")) == 1
    assert len(fake.creates("ERRPYO005")) == 1  # done before the crash, not repeated
    assert probe(state, "create", "ERRPYO039")["matched"] is True  # later values still run
    assert findings(root)["unknown_values"] == ["ERRPYO014"]
    assert_no_private_values(root, output)


@pytest.mark.asyncio
async def test_one_generic_error_for_everything_is_blocked(settings, root):
    fake, output = FakePayPal(generic=True), Output()

    await run(settings, root, fake, output)

    finding = findings(root)
    assert finding["verdict"] == "BLOCKED"
    assert any("negative testing appears not to be active" in note for note in finding["notes"])
    assert_no_private_values(root, output)


@pytest.mark.asyncio
async def test_without_maker_b_no_create_is_sent(root):
    settings = SandboxSettings(client_id=CLIENT_ID, client_secret=SECRET)
    fake, output = FakePayPal(), Output()

    state = await run(settings, root, fake, output)

    assert not fake.creates()
    assert probe(state, "create", "ERRPYO005")["reason"] == "SBX_MAKER_B_EMAIL is not set"
    finding = findings(root)
    assert finding["verdict"] == "BLOCKED"
    assert set(finding["unknown_values"]) == set(CREATES)
