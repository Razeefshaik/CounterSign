import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from scripts.sandbox_checks.catcher import WebhookStore
from scripts.sandbox_checks.checks import d11_webhooks as d11
from scripts.sandbox_checks.env import SandboxSettings
from scripts.sandbox_checks.manual import ManualSteps
from scripts.sandbox_checks.redact import Redactor
from scripts.sandbox_checks.runner import CheckFailed, Runner

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
CLIENT_ID = "FAKE-CLIENT-ID"
SECRET = "FAKE-CLIENT-SECRET"
TOKEN = "FAKE-ACCESS-TOKEN"
BUYER = "buyer-1@fake.example"
WEBHOOK_ID = "WH-HOOK-FAKE"
VERIFY_RESULTS = {"good": "SUCCESS", "bad": "FAILURE", "sim": "FAILURE"}


def at(hour, minute, second=0):
    return datetime(2026, 10, 24, hour, minute, second, tzinfo=UTC)


def iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


class FakeClock:
    def __init__(self):
        self.now_value = NOW
        self.sleeps: list[float] = []

    def now(self):
        return self.now_value

    async def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now_value += timedelta(seconds=seconds)


def event_bytes(event_id, event_type, created: datetime, resource: dict) -> bytes:
    # Odd spacing on purpose: verification must send the parsed event, built from these bytes.
    event = {
        "id": event_id,
        "event_type": event_type,
        "create_time": iso(created),
        "resource_type": "fake",
        "resource": resource,
    }
    return json.dumps(event, separators=(" ,", ":  ")).encode("utf-8")


def signed_headers(signature: str, transmission: str) -> dict[str, str]:
    return {
        "content-type": "application/json",
        "paypal-auth-algo": "SHA256withRSA",
        "paypal-cert-url": "https://api.sandbox.paypal.com/v1/notifications/certs/CERT-fake",
        "paypal-transmission-id": transmission,
        "paypal-transmission-sig": signature,
        "paypal-transmission-time": "2026-10-24T09:00:01Z",
    }


class Deliveries:
    """Writes deliveries in the catcher's layout, as PayPal would through the tunnel."""

    def __init__(self, root, settings):
        self.received = NOW
        self.store = WebhookStore(
            redactor=Redactor.from_settings(settings), root=root, clock=lambda: self.received
        )
        self.count = 0

    def deliver(self, event_id, event_type, created, received, resource, signature="good"):
        self.received = received
        self.count += 1
        raw = event_bytes(event_id, event_type, created, resource)
        return self.store.store(raw, signed_headers(signature, f"tx-{self.count}"))


class FakePayPal:
    def __init__(self, deliveries: Deliveries, clock: FakeClock):
        self.deliveries = deliveries
        self.clock = clock
        self.requests: list[httpx.Request] = []
        self.simulator_delivers = True
        self.lose_simulate_response = False
        self.lose_resend_response = False
        self.verify_override: dict[str, object] = {}  # transmission id -> status code or error

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path, method = request.url.path, request.method
        if path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": TOKEN, "expires_in": 32400})
        if path == "/v1/reporting/balances":
            return httpx.Response(200, json={"balances": []})
        if path == d11.VERIFY_PATH:
            body = json.loads(request.content)
            override = self.verify_override.get(body["transmission_id"])
            if isinstance(override, BaseException):
                raise override
            if override:
                return httpx.Response(override, json={"name": "INTERNAL_SERVICE_ERROR"})
            status = VERIFY_RESULTS[body["transmission_sig"]]
            return httpx.Response(200, json={"verification_status": status})
        if path == d11.SIMULATE_PATH:
            body = json.loads(request.content)
            if self.simulator_delivers:
                self.deliveries.deliver(
                    "WH-SIM-1",
                    body["event_type"],
                    self.clock.now(),
                    self.clock.now(),
                    {"id": "SIM-CAPTURE", "status": "COMPLETED"},
                    signature="sim",
                )
            if self.lose_simulate_response:
                raise httpx.ReadTimeout("fake lost response")
            return httpx.Response(202, json={"id": "WH-SIM-1", "event_type": body["event_type"]})
        if path == d11.EVENTS_PATH and method == "GET":
            events = [
                {"id": "WH-CAP-1", "event_type": "PAYMENT.CAPTURE.COMPLETED"},
                {"id": "WH-NEVER-DELIVERED", "event_type": "PAYMENT.PAYOUTS-ITEM.SUCCEEDED"},
            ]
            return httpx.Response(200, json={"events": events, "count": 2, "links": []})
        if path.startswith(d11.EVENTS_PATH + "/") and path.endswith("/resend"):
            event_id = path.split("/")[-2]
            self.deliveries.deliver(
                event_id,
                "PAYMENT.CAPTURE.COMPLETED",
                at(9, 0),
                self.clock.now(),
                {"id": "CAP1", "status": "COMPLETED"},
            )
            if self.lose_resend_response:
                raise httpx.ReadTimeout("fake lost response")
            return httpx.Response(202, json={"id": event_id})
        return httpx.Response(404, json={"name": "RESOURCE_NOT_FOUND"})

    def calls(self, path, method="POST"):
        return [r for r in self.requests if r.method == method and r.url.path == path]

    def resends(self):
        return [r for r in self.requests if r.url.path.endswith("/resend")]


class Operator:
    def __init__(self):
        self.lines: list[str] = []

    def output(self, line):
        self.lines.append(line)

    def input(self, _prompt):
        return ""


def seed(root, check_id, data):
    path = root / "2026-10-24" / check_id / "state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    state = {"schema_version": 1, "check_id": check_id, "completed_steps": [], "data": data}
    path.write_text(json.dumps(state), encoding="utf-8")


def event(source, name, resource_id, observed):
    return {"at": iso(observed), "source": source, "event": name, "resource_id": resource_id}


@pytest.fixture
def settings():
    return SandboxSettings(
        client_id=CLIENT_ID, client_secret=SECRET, buyer_email=BUYER, webhook_id=WEBHOOK_ID
    )


@pytest.fixture
def root(tmp_path):
    seed(tmp_path, "D1", {"verdict": "CONFIRMED"})
    seed(tmp_path, "D2", {"timeline": [event("D2", "CAPTURE_COMPLETED", "CAP1", at(9, 0, 30))]})
    seed(
        tmp_path,
        "D3",
        {
            "timeline": [
                event("D3", "PAYOUT_SUBMITTED", "BATCH1", at(9, 31)),
                event("D3", "PAYOUT_ITEM_SUCCESS", "ITEM1", at(9, 40)),
            ]
        },
    )
    seed(tmp_path, "D8", {"timeline": [event("D8", "DISPUTE_CREATED", "PP-D-1", at(9, 11))]})
    seed(tmp_path, "D10", {"timeline": [event("D10", "CAPTURE_REFUNDED", "CAP60", at(10, 0))]})
    return tmp_path


def store_day(deliveries: Deliveries, *, last_signature="good"):
    """A day of deliveries: a duplicate, a reordered batch pair and a refund naming its capture."""
    dispute = {"dispute_id": "PP-D-1", "messages": [{"content": f"Contact me at {BUYER}"}]}
    batch = {"batch_header": {"payout_batch_id": "BATCH1"}}
    rows = [
        ("WH-CAP-1", "PAYMENT.CAPTURE.COMPLETED", at(9, 0), at(9, 0, 5), {"id": "CAP1"}),
        ("WH-DSP-1", "CUSTOMER.DISPUTE.CREATED", at(9, 10), at(9, 12), dispute),
        ("WH-DSP-1", "CUSTOMER.DISPUTE.CREATED", at(9, 10), at(9, 20), dispute),
        ("WH-BAT-S", "PAYMENT.PAYOUTSBATCH.SUCCESS", at(9, 33), at(9, 34), batch),
        ("WH-BAT-P", "PAYMENT.PAYOUTSBATCH.PROCESSING", at(9, 32), at(9, 36), batch),
        (
            "WH-ITM-2",
            "PAYMENT.PAYOUTS-ITEM.SUCCEEDED",
            at(9, 41),
            at(9, 42),
            {"payout_item_id": "ITEM2"},
        ),
    ]
    for row in rows:
        deliveries.deliver(*row)
    refund = {
        "id": "REF1",
        "links": [
            {"rel": "up", "href": "https://api.sandbox.paypal.com/v2/payments/captures/CAP60"}
        ],
    }
    deliveries.deliver(
        "WH-REF-1", "PAYMENT.CAPTURE.REFUNDED", at(10, 0), at(10, 1), refund, last_signature
    )


def runner(settings, root, fake, operator, clock):
    return Runner(
        settings,
        root=root,
        transport=httpx.MockTransport(fake.handle),
        clock=clock.now,
        output=operator.output,
        manual=ManualSteps(
            Redactor.from_settings(settings), input_fn=operator.input, output_fn=operator.output
        ),
        sleep=clock.sleep,
    )


def setup(settings, root):
    clock = FakeClock()
    deliveries = Deliveries(root, settings)
    return clock, deliveries, FakePayPal(deliveries, clock), Operator()


def findings(root):
    return json.loads((root / "2026-10-24" / "D11" / "findings.json").read_text(encoding="utf-8"))


def assert_no_private_values(root, operator):
    public = [p for p in root.rglob("*") if p.is_file() and "raw" not in p.relative_to(root).parts]
    text = "".join(p.read_text(encoding="utf-8") for p in public) + "\n".join(operator.lines)
    for private in (CLIENT_ID, SECRET, TOKEN, BUYER):
        assert private not in text


def verify_bodies(fake):
    return [json.loads(r.content) for r in fake.calls(d11.VERIFY_PATH)]


@pytest.mark.asyncio
async def test_d11_analyses_deliveries_and_confirms_real_signatures(settings, root):
    clock, deliveries, fake, operator = setup(settings, root)
    store_day(deliveries)

    state = await runner(settings, root, fake, operator, clock).run("D11")

    final = findings(root)
    assert final["verdict"] == "CONFIRMED"
    analysis = final["analysis"]
    delay = analysis["delay_seconds"]
    assert delay["by_event_type"]["PAYMENT.CAPTURE.COMPLETED"]["min"] == 5.0
    assert delay["by_event_type"]["CUSTOMER.DISPUTE.CREATED"] == {
        "count": 2,
        "min": 120.0,
        "median": 360.0,
        "max": 600.0,
    }
    duplicates = analysis["duplicates"]
    assert duplicates["WH-DSP-1"]["count"] == 2 and not duplicates["WH-DSP-1"]["induced_by_resend"]
    assert duplicates["WH-CAP-1"]["induced_by_resend"] is True  # the resend's copy
    [reordered] = analysis["out_of_order"]
    assert reordered["resource_id"] == "BATCH1" and reordered["event_id"] == "WH-BAT-P"
    assert reordered["received_after"] == "WH-BAT-S"
    assert [(m["event"], m["resource_id"]) for m in analysis["missing"]] == [
        ("PAYOUT_ITEM_SUCCESS", "ITEM1")
    ]
    matched = {m["event"]: m for m in analysis["matched"]}
    assert matched["CAPTURE_REFUNDED"]["arrived"] == ["PAYMENT.CAPTURE.REFUNDED"]
    assert matched["PAYOUT_SUBMITTED"]["arrived"] == [
        "PAYMENT.PAYOUTSBATCH.PROCESSING",
        "PAYMENT.PAYOUTSBATCH.SUCCESS",
    ]
    assert matched["CAPTURE_COMPLETED"]["webhook_minus_observed_seconds"] == -25.0
    # Every real delivery, including the resend's copy, verified; the simulator only once.
    signatures = final["signatures"]
    assert signatures["real_deliveries"] == 8 and signatures["success"] == 8
    bodies = verify_bodies(fake)
    assert len(bodies) == 9
    first = next(body for body in bodies if body["transmission_id"] == "tx-1")
    raw = (root / "raw" / "webhooks" / f"{analysis['per_delivery'][0]['name']}.body").read_bytes()
    assert first["webhook_event"] == json.loads(raw)
    assert first["webhook_id"] == WEBHOOK_ID
    assert first["transmission_sig"] == "good" and first["auth_algo"] == "SHA256withRSA"
    assert set(first) == {*d11.SIGNATURE_HEADERS, "webhook_id", "webhook_event"}
    simulator = final["simulator"]
    assert simulator["arrived"] and simulator["matched_by"] == "event_id"
    assert simulator["verification"]["verification_status"] == "FAILURE"
    assert simulator["matches_f11"] is True
    assert json.loads(fake.calls(d11.SIMULATE_PATH)[0].content) == {
        "webhook_id": WEBHOOK_ID,
        "event_type": "PAYMENT.CAPTURE.COMPLETED",
    }
    [resend] = fake.resends()
    assert resend.url.path == "/v1/notifications/webhooks-events/WH-CAP-1/resend"
    assert json.loads(resend.content) == {"webhook_ids": [WEBHOOK_ID]}
    assert final["backfill"]["resend"]["duplicate_arrived"] is True
    event_list = final["backfill"]["event_list"]
    assert event_list["works"] is True
    assert event_list["listed_not_stored"] == [
        {"event_id": "WH-NEVER-DELIVERED", "event_type": "PAYMENT.PAYOUTS-ITEM.SUCCEEDED"}
    ]
    assert any("catcher" in line for line in operator.lines)
    assert any("re-serialised" in note for note in final["notes"])
    assert state["data"]["verifications"]
    assert_no_private_values(root, operator)


@pytest.mark.asyncio
async def test_a_failed_real_signature_is_different(settings, root):
    clock, deliveries, fake, operator = setup(settings, root)
    store_day(deliveries, last_signature="bad")

    await runner(settings, root, fake, operator, clock).run("D11")

    final = findings(root)
    assert final["verdict"] == "DIFFERENT"
    assert final["signatures"]["failure"] == 1
    [failed] = final["signatures"]["failed"]
    assert failed.endswith("WH-REF-1")
    assert_no_private_values(root, operator)


@pytest.mark.asyncio
async def test_verify_call_errors_stay_unknown_not_failure(settings, root):
    clock, deliveries, fake, operator = setup(settings, root)
    store_day(deliveries)
    fake.verify_override = {"tx-1": 503, "tx-2": httpx.ConnectError("fake outage")}

    await runner(settings, root, fake, operator, clock).run("D11")

    final = findings(root)
    signatures = final["signatures"]
    assert final["verdict"] == "BLOCKED"
    assert signatures["failure"] == 0 and signatures["unknown"] == 2
    results = [signatures["results"][name] for name in signatures["unknown_names"]]
    assert sorted(str(r["status"]) for r in results) == ["503", "None"]
    assert {r["verification_status"] for r in results} == {None}
    assert {r.get("error") for r in results} == {None, "SandboxRequestError"}
    assert_no_private_values(root, operator)


@pytest.mark.asyncio
async def test_missing_signature_headers_are_unknown_and_not_sent(settings, root):
    clock, deliveries, fake, operator = setup(settings, root)
    raw = event_bytes("WH-CAP-1", "PAYMENT.CAPTURE.COMPLETED", at(9, 0), {"id": "CAP1"})
    deliveries.received = at(9, 1)
    deliveries.store.store(raw, {"content-type": "application/json"})
    fake.simulator_delivers = False

    await runner(settings, root, fake, operator, clock).run("D11")

    final = findings(root)
    signatures = final["signatures"]
    [unsigned] = [signatures["results"][name] for name in signatures["unknown_names"]]
    assert unsigned["error"] == "MISSING_HEADERS" and len(unsigned["missing_headers"]) == 5
    assert signatures["success"] == 1  # the resend's signed copy
    assert final["verdict"] == "BLOCKED"
    assert [body["transmission_id"] for body in verify_bodies(fake)] == ["tx-1"]


@pytest.mark.asyncio
async def test_resume_skips_verified_deliveries_and_never_resends(settings, root):
    clock, deliveries, fake, operator = setup(settings, root)
    store_day(deliveries)
    fake.lose_simulate_response = True
    with pytest.raises(CheckFailed):
        await runner(settings, root, fake, operator, clock).run("D11")

    fake.lose_simulate_response = False
    fake.lose_resend_response = True
    with pytest.raises(CheckFailed):
        await runner(settings, root, fake, operator, clock).run("D11")

    fake.lose_resend_response = False
    fake.verify_override = {"tx-5": RuntimeError("fake interruption")}
    with pytest.raises(CheckFailed):
        await runner(settings, root, fake, operator, clock).run("D11")
    verified_before = len(verify_bodies(fake))

    fake.verify_override = {}
    await runner(settings, root, fake, operator, clock).run("D11")

    assert len(fake.calls(d11.SIMULATE_PATH)) == 1
    assert len(fake.resends()) == 1
    transmissions = [body["transmission_id"] for body in verify_bodies(fake)]
    # tx-1 to tx-4 settled before the interruption; tx-5 never got an answer and is retried.
    assert transmissions.count("tx-1") == 1 and transmissions.count("tx-5") == 2
    assert len(transmissions) == verified_before + 4  # tx-5 and the three after it
    final = findings(root)
    assert final["simulator"]["matched_by"] == "type_and_time"
    assert final["simulator"]["status"] is None and final["simulator"]["arrived"] is True
    assert final["backfill"]["resend"]["status"] is None
    assert final["backfill"]["resend"]["duplicate_arrived"] is True
    assert final["verdict"] == "CONFIRMED"
    assert_no_private_values(root, operator)


@pytest.mark.asyncio
async def test_without_webhook_id_signatures_are_blocked_and_nothing_is_sent(root):
    settings = SandboxSettings(client_id=CLIENT_ID, client_secret=SECRET, buyer_email=BUYER)
    clock, deliveries, fake, operator = setup(settings, root)
    store_day(deliveries)

    await runner(settings, root, fake, operator, clock).run("D11")

    final = findings(root)
    assert final["verdict"] == "BLOCKED"
    assert "WEBHOOK_ID" in final["verdict_reason"]
    assert final["simulator"] == {"skipped": "NO_WEBHOOK_ID"}
    assert final["backfill"]["resend"] == {"skipped": "NO_WEBHOOK_ID"}
    assert not fake.calls(d11.SIMULATE_PATH) and not fake.resends() and not verify_bodies(fake)
    # The statistics are reported whatever the verdict.
    assert final["analysis"]["deliveries"] == 7
    assert final["analysis"]["missing"]
    assert_no_private_values(root, operator)


@pytest.mark.asyncio
async def test_without_deliveries_signatures_are_blocked(settings, root):
    clock, _deliveries, fake, operator = setup(settings, root)
    fake.simulator_delivers = False

    await runner(settings, root, fake, operator, clock).run("D11")

    final = findings(root)
    assert final["verdict"] == "BLOCKED"
    assert final["backfill"]["resend"] == {"skipped": "NO_REAL_DELIVERY"}
    assert final["simulator"]["arrived"] is False
    assert clock.sleeps.count(d11.ARRIVAL_POLL_SECONDS) == d11.ARRIVAL_MAX_POLLS - 1
    assert len(final["analysis"]["missing"]) == 5


def test_resource_ids_follow_each_resource_schema():
    assert d11.resource_ids(
        "PAYMENT.PAYOUTS-ITEM.UNCLAIMED",
        {"resource": {"payout_item_id": "I1", "payout_batch_id": "B1"}},
    ) == ["I1"]
    assert d11.resource_ids(
        "PAYMENT.PAYOUTSBATCH.SUCCESS", {"resource": {"batch_header": {"payout_batch_id": "B1"}}}
    ) == ["B1"]
    assert d11.resource_ids("CUSTOMER.DISPUTE.UPDATED", {"resource": {"dispute_id": "PP-D-9"}}) == [
        "PP-D-9"
    ]
    assert d11.resource_ids("PAYMENT.CAPTURE.COMPLETED", {"resource": {"id": "C1"}}) == ["C1"]
    refund = {"id": "R1", "links": [{"rel": "up", "href": "https://x/v2/payments/captures/C1"}]}
    assert d11.resource_ids("PAYMENT.CAPTURE.REFUNDED", {"resource": refund}) == ["R1", "C1"]
    assert d11.resource_ids(None, "not a body") == []
