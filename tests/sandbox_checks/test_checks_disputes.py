import json
import re
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from scripts.sandbox_checks.checks import d08_dispute_create as d08
from scripts.sandbox_checks.env import SandboxSettings
from scripts.sandbox_checks.manual import ManualSteps
from scripts.sandbox_checks.redact import Redactor
from scripts.sandbox_checks.runner import CheckFailed, Runner

NOW = datetime(2026, 10, 24, 9, 0, tzinfo=UTC)
REAL_HOLD_SECONDS = d08.HOLD_OBSERVE_SECONDS  # read before the autouse fixture shortens it
CLIENT_ID = "FAKE-CLIENT-ID"
SECRET = "FAKE-CLIENT-SECRET"
TOKEN = "FAKE-ACCESS-TOKEN"
BUYER = "buyer-1@fake.example"
CAPTURES = {"inr_400": "CAP400", "snad_60": "CAP60", "spare_25": "CAP25"}
AMOUNTS = {"CAP400": 40000, "CAP60": 6000, "CAP25": 2500}
BASE_SCOPES = (
    "https://uri.paypal.com/services/payments/payouts "
    "https://uri.paypal.com/services/disputes/read-seller "
    "https://uri.paypal.com/services/disputes/update-seller "
    "https://uri.paypal.com/services/reporting/search/read"
)


def dollars(cents: int) -> str:
    return f"{cents / 100:.2f}"


class FakeClock:
    def __init__(self):
        self.now_value = NOW
        self.sleeps: list[float] = []

    def now(self):
        return self.now_value

    async def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now_value += timedelta(seconds=seconds)


class FakePayPal:
    """Just enough sandbox dispute behaviour to drive D8 to D10 deterministically."""

    def __init__(self, *, evidence_at_inquiry=True, escalate_status="UNDER_REVIEW", appear_after=2):
        self.evidence_at_inquiry = evidence_at_inquiry
        self.escalate_status = escalate_status
        self.appear_after = appear_after
        self.requests: list[httpx.Request] = []
        self.disputes: dict[str, dict] = {}
        self.available = 500000
        self.withheld = 0
        self.list_calls = 0
        self.create_scope = False

    # Buyer actions in the Resolution Center (triggered from operator instructions).
    def open(self, capture_id, reason="MERCHANDISE_OR_SERVICE_NOT_RECEIVED"):
        dispute_id = f"PP-D-{len(self.disputes) + 1:04d}"
        amount = AMOUNTS[capture_id]
        self.disputes[dispute_id] = {
            "capture": capture_id,
            "amount": amount,
            "reason": reason,
            "status": "WAITING_FOR_SELLER_RESPONSE",
            "stage": "INQUIRY",
            "outcome": None,
            "visible_after": self.list_calls + self.appear_after,
        }
        self.available -= amount
        self.withheld += amount
        return dispute_id

    def resolve(self, dispute_id, code, *, refund):
        dispute = self.disputes[dispute_id]
        dispute.update(status="RESOLVED", outcome=code, refunded=dispute["amount"] if refund else 0)
        self.withheld -= dispute["amount"]
        if not refund:
            self.available += dispute["amount"]

    def detail(self, dispute_id):
        d = self.disputes[dispute_id]
        base = f"https://api-m.sandbox.paypal.com/v1/customer/disputes/{dispute_id}"
        rels = ["self"]
        if d["status"] != "RESOLVED":
            rels.append("accept_claim")
            if d["stage"] == "INQUIRY":
                rels.append("escalate")
            if d["status"] == "WAITING_FOR_SELLER_RESPONSE" and (
                d["stage"] != "INQUIRY" or self.evidence_at_inquiry
            ):
                rels.append("provide_evidence")
            if d["status"] == "UNDER_REVIEW":
                rels += ["adjudicate", "require_evidence"]
        body = {
            "dispute_id": dispute_id,
            "reason": d["reason"],
            "status": d["status"],
            "dispute_life_cycle_stage": d["stage"],
            "dispute_amount": {"currency_code": "USD", "value": dollars(d["amount"])},
            "seller_response_due_date": "2026-11-03T23:59:59Z",
            "disputed_transactions": [
                {"seller_transaction_id": d["capture"], "buyer_transaction_id": "B" + d["capture"]}
            ],
            "messages": [{"posted_by": "BUYER", "content": f"Contact me at {BUYER}"}],
            "links": [{"href": base, "rel": rel, "method": "GET"} for rel in rels],
        }
        if d["outcome"]:
            body["dispute_outcome"] = {
                "outcome_code": d["outcome"],
                "amount_refunded": {"currency_code": "USD", "value": dollars(d.get("refunded", 0))},
            }
        return body

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path, method = request.url.path, request.method
        if path == "/v1/oauth2/token":
            scope = BASE_SCOPES + (
                " https://uri.paypal.com/services/disputes/create" if self.create_scope else ""
            )
            return httpx.Response(
                200, json={"access_token": TOKEN, "expires_in": 32400, "scope": scope}
            )
        if path == "/v1/reporting/balances":
            usd = lambda cents: {"currency_code": "USD", "value": dollars(cents)}
            return httpx.Response(
                200,
                json={
                    "balances": [
                        {
                            "currency": "USD",
                            "available_balance": usd(self.available),
                            "withheld_balance": usd(self.withheld),
                            "total_balance": usd(self.available + self.withheld),
                        }
                    ],
                    "last_refresh_time": "2026-10-24T08:00:00Z",
                },
            )
        if path.startswith("/v2/payments/captures/"):
            return httpx.Response(200, json={"id": path.rsplit("/", 1)[-1], "status": "COMPLETED"})
        if path == "/v1/customer/disputes" and method == "GET":
            self.list_calls += 1
            items = [
                {"dispute_id": dispute_id, "status": d["status"]}
                for dispute_id, d in self.disputes.items()
                if d["visible_after"] <= self.list_calls
            ]
            return httpx.Response(200, json={"items": items})
        if path == "/v1/customer/disputes" and method == "POST":
            dispute_id = self.open("CAP25")
            self.disputes[dispute_id]["visible_after"] = 0
            return httpx.Response(
                201,
                json={
                    "links": [
                        {"href": f"https://x/v1/customer/disputes/{dispute_id}", "rel": "self"}
                    ]
                },
            )
        match = re.fullmatch(r"/v1/customer/disputes/([^/]+)(?:/([a-z-]+))?", path)
        if match and match.group(1) in self.disputes:
            dispute_id, action = match.groups()
            if action is None:
                return httpx.Response(200, json=self.detail(dispute_id))
            return self.act(dispute_id, action, request)
        return httpx.Response(404, json={"name": "RESOURCE_NOT_FOUND"})

    def act(self, dispute_id, action, request):
        d = self.disputes[dispute_id]
        rels = {link["rel"] for link in self.detail(dispute_id)["links"]}
        if action.replace("-", "_") not in rels:
            return httpx.Response(422, json={"name": "UNPROCESSABLE_ENTITY"})
        if action == "provide-evidence":
            if not request.headers["content-type"].startswith("multipart/form-data"):
                return httpx.Response(400, json={"name": "INVALID_REQUEST"})
            d["status"] = (
                "UNDER_REVIEW" if d["stage"] != "INQUIRY" else "WAITING_FOR_BUYER_RESPONSE"
            )
        elif action == "escalate":
            d["stage"] = "CHARGEBACK"
            d["status"] = self.escalate_status
        elif action == "require-evidence":
            d["status"] = "WAITING_FOR_SELLER_RESPONSE"
        elif action == "adjudicate":
            favour = json.loads(request.content)["adjudication_outcome"]
            if favour == "SELLER_FAVOR":
                self.resolve(dispute_id, "RESOLVED_SELLER_FAVOUR", refund=False)
            else:
                self.resolve(dispute_id, "RESOLVED_BUYER_FAVOUR", refund=True)
        elif action == "accept-claim":
            self.resolve(dispute_id, "RESOLVED_BUYER_FAVOUR", refund=True)
        return httpx.Response(200, json={"links": []})

    def posts(self, suffix):
        return [r for r in self.requests if r.method == "POST" and r.url.path.endswith(suffix)]


@pytest.fixture
def settings():
    return SandboxSettings(client_id=CLIENT_ID, client_secret=SECRET, buyer_email=BUYER)


def seed(root, check_id, data):
    path = root / "2026-10-24" / check_id / "state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    state = {"schema_version": 1, "check_id": check_id, "completed_steps": [], "data": data}
    path.write_text(json.dumps(state), encoding="utf-8")


@pytest.fixture
def root(tmp_path):
    seed(tmp_path, "D1", {"verdict": "CONFIRMED", "permissions": {"disputes_create": False}})
    seed(
        tmp_path,
        "D2",
        {
            "captures": {
                "inr_400": {"capture_id": "CAP400"},
                "snad_60": "CAP60",
                "spare_25": "CAP25",
            },
            "tracking": {
                "inr_400": {"carrier": "USPS", "tracking_number": "9400100000000000000001"}
            },
        },
    )
    return tmp_path


@pytest.fixture(autouse=True)
def short_observation(monkeypatch):
    # Real defaults (asserted below) are 5 minutes for 3 hours; three samples are enough here.
    for module in (d08,):
        monkeypatch.setattr(module, "HOLD_OBSERVE_SECONDS", 600)


class Operator:
    """Answers prompts and performs the buyer's browser actions the instructions ask for."""

    def __init__(self, fake: FakePayPal, answers: dict[str, str] | None = None):
        self.fake = fake
        self.answers = answers or {}  # prompt substring -> typed answer; Enter otherwise
        self.lines: list[str] = []

    def output(self, line):
        self.lines.append(line)
        if "Report a problem" in line or "report 'item not received'" in line:
            capture = re.search(r"capture (CAP\d+)", line).group(1)
            reason = "MERCHANDISE_OR_SERVICE_NOT_AS_DESCRIBED" if "as described" in line else None
            self.fake.open(capture, reason or "MERCHANDISE_OR_SERVICE_NOT_RECEIVED")
        if "close or cancel it" in line:
            dispute_id = re.search(r"dispute (PP-D-\d+)", line).group(1)
            self.fake.resolve(dispute_id, "CANCELED_BY_BUYER", refund=False)

    def input(self, _prompt):
        last = self.lines[-1] if self.lines else ""
        return next((answer for key, answer in self.answers.items() if key in last), "")


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


def public_text(root, operator):
    files = "".join(p.read_text(encoding="utf-8") for p in root.rglob("*") if p.is_file())
    return files + "\n".join(operator.lines)


def assert_no_private_values(root, operator):
    text = public_text(root, operator)
    for private in (CLIENT_ID, SECRET, TOKEN, BUYER):
        assert private not in text


def findings(root, check_id):
    return json.loads((root / "2026-10-24" / check_id / "findings.json").read_text())


def test_observation_defaults_match_plan():
    # Every 5 minutes for 3 hours: 37 balance and capture observations.
    assert REAL_HOLD_SECONDS == 3 * 3600
    assert d08.HOLD_INTERVAL_SECONDS == 300
    assert REAL_HOLD_SECONDS // d08.HOLD_INTERVAL_SECONDS + 1 == 37
    assert d08.POLL_SECONDS == 60
    assert d08.ROLES["inr_400"]["reason"] == "MERCHANDISE_OR_SERVICE_NOT_RECEIVED"
    assert d08.ROLES["snad_60"]["reason"] == "MERCHANDISE_OR_SERVICE_NOT_AS_DESCRIBED"


async def run_checks(settings, root, fake, operator, clock, *checks):
    states = {}
    for check in checks:
        states[check] = await runner(settings, root, fake, operator, clock).run(check)
    return states


@pytest.mark.asyncio
async def test_d8_opens_disputes_in_the_resolution_center_and_records_the_hold(settings, root):
    fake, clock = FakePayPal(), FakeClock()
    operator = Operator(fake)

    d8_state = await runner(settings, root, fake, operator, clock).run("D8")

    d8_data = d8_state["data"]
    assert d8_data["api_route"]["verdict"] == "BLOCKED"
    assert not fake.posts("/v1/customer/disputes")
    for role in CAPTURES:
        dispute = d8_data["disputes"][role]
        assert dispute["route"] == "RESOLUTION_CENTER"
        assert dispute["matched_field"] == "seller_transaction_id"
        assert dispute["seconds_to_appear"] > 0
        assert dispute["initial"]["dispute_life_cycle_stage"] == "INQUIRY"
        assert "provide_evidence" in dispute["initial"]["rels"]
    assert 60 in clock.sleeps
    assert len(d8_data["hold_observations"]) == 3  # the shortened 10-minute window
    assert clock.sleeps.count(300) == 2
    d8_finding = findings(root, "D8")
    assert d8_finding["verdict"] == "CONFIRMED"
    assert d8_finding["hold"]["hold_observed"] is True
    assert d8_finding["evidence_files"]
    assert_no_private_values(root, operator)


@pytest.mark.asyncio
async def test_api_route_runs_only_with_scope_and_hides_the_assertion(settings, root):
    seed(root, "D1", {"verdict": "CONFIRMED", "permissions": {"disputes_create": True}})
    fake, clock = FakePayPal(), FakeClock()
    fake.create_scope = True
    operator = Operator(fake, answers={"transaction ID": "BCAP25"})

    state = await runner(settings, root, fake, operator, clock).run("D8")

    [create] = fake.posts("/v1/customer/disputes")
    assertion = create.headers["paypal-auth-assertion"]
    assert assertion.endswith(".") and assertion.count(".") == 2
    assert b'name="input"' in create.content and b"BCAP25" in create.content
    data = state["data"]
    assert data["api_route"]["verdict"] == "CONFIRMED"
    assert data["disputes"]["spare_25"]["route"] == "API"
    assert data["disputes"]["inr_400"]["route"] == "RESOLUTION_CENTER"
    assert sum("Report a problem" in line for line in operator.lines) == 2
    assert assertion not in public_text(root, operator)
    assert_no_private_values(root, operator)


@pytest.mark.asyncio
async def test_interrupted_api_create_is_never_resent_and_is_found_by_polling(settings, root):
    seed(root, "D1", {"verdict": "CONFIRMED", "permissions": {"disputes_create": True}})
    fake, clock = FakePayPal(), FakeClock()
    fake.create_scope = True
    operator = Operator(fake, answers={"transaction ID": "BCAP25"})

    real_handle = fake.handle

    def create_then_drop(request):
        # PayPal creates the dispute, then the response is lost.
        if request.method == "POST" and request.url.path == "/v1/customer/disputes":
            fake.requests.append(request)
            fake.open("CAP25")
            raise httpx.ReadTimeout("fake lost response")
        return real_handle(request)

    fake.handle = create_then_drop
    with pytest.raises(CheckFailed):
        await runner(settings, root, fake, operator, clock).run("D8")

    state = await runner(settings, root, fake, operator, clock).run("D8")

    creates = [
        r for r in fake.requests if r.method == "POST" and r.url.path == "/v1/customer/disputes"
    ]
    assert len(creates) == 1
    assert state["data"]["api_route"]["verdict"] == "UNKNOWN"
    assert state["data"]["disputes"]["spare_25"]["dispute_id"] == "PP-D-0001"
    assert sum("Report a problem" in line for line in operator.lines) == 2
