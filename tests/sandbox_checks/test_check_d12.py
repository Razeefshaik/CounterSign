import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from scripts.sandbox_checks.checks import d12_balances as d12
from scripts.sandbox_checks.env import SandboxSettings
from scripts.sandbox_checks.manual import ManualSteps
from scripts.sandbox_checks.recorder import timestamp
from scripts.sandbox_checks.redact import Redactor
from scripts.sandbox_checks.runner import CheckFailed, Runner

NOW = datetime(2026, 10, 24, 9, 0, tzinfo=UTC)
CLIENT_ID = "FAKE-CLIENT-ID"
SECRET = "FAKE-CLIENT-SECRET"
TOKEN = "FAKE-ACCESS-TOKEN"
BUYER = "buyer-1@fake.example"


def at(minutes: float) -> str:
    return timestamp(NOW + timedelta(minutes=minutes))


def dollars(cents: int) -> str:
    return f"{cents / 100:.2f}"


def usd(cents: int) -> dict:
    return {"currency_code": "USD", "value": dollars(cents)}


def balance_body(available, withheld, *, total=None, refreshed=None) -> dict:
    return {
        "balances": [
            {
                "currency": "USD",
                "primary": True,
                "total_balance": usd(available + withheld if total is None else total),
                "available_balance": usd(available),
                "withheld_balance": usd(withheld),
            }
        ],
        "account_id": "FAKEACCOUNT1",
        "as_of_time": refreshed,
        "last_refresh_time": refreshed,
    }


def reading(minutes, available=0, withheld=0, *, total=None, lag=10, status=200) -> dict:
    """One balances.jsonl line, as the runner's BalancePoller writes it."""
    if status is None:
        return {"fetched_at": at(minutes), "response_status": None, "error": "SandboxRequestError"}
    if status != 200:
        body = {"name": "SERVICE_UNAVAILABLE"}
    else:
        body = balance_body(available, withheld, total=total, refreshed=at(minutes - lag))
    return {"fetched_at": at(minutes), "response_status": status, "response_body": body}


def write_readings(root, rows):
    lines = [json.dumps(row) for row in rows]
    (root / "balances.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def event(source, name, resource, minutes, amount=None, currency="USD") -> dict:
    return {
        "at": at(minutes),
        "source": source,
        "event": name,
        "resource_id": resource,
        "amount_minor": amount,
        "currency": currency,
        "paypal_time": None,
    }


def seed(root, check_id, data):
    path = root / "2026-10-24" / check_id / "state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    state = {"schema_version": 1, "check_id": check_id, "completed_steps": [], "data": data}
    path.write_text(json.dumps(state), encoding="utf-8")


class FakeClock:
    def __init__(self):
        self.now_value = NOW
        self.sleeps: list[float] = []
        self.interrupt_on_sleep: int | None = None

    def now(self):
        return self.now_value

    async def sleep(self, seconds):
        if self.interrupt_on_sleep == len(self.sleeps) + 1:
            self.interrupt_on_sleep = None
            raise RuntimeError("fake interruption")
        self.sleeps.append(seconds)
        self.now_value += timedelta(seconds=seconds)


class FakePayPal:
    """Balances and Transaction Search, enough to drive D12 deterministically."""

    def __init__(self, clock: FakeClock, *, available=0, withheld=0):
        self.clock = clock
        self.available = available
        self.withheld = withheld
        self.balance_status = 200
        self.capture_id = "CAP400"
        self.visible_on = 3  # Transaction Search shows the capture from this call on
        self.search_status = 200
        self.searches: list[httpx.Request] = []
        self.balance_reads: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v1/oauth2/token":
            return httpx.Response(
                200, json={"access_token": TOKEN, "expires_in": 32400, "scope": "openid"}
            )
        if path == "/v1/reporting/balances":
            self.balance_reads.append(request)
            if self.balance_status != 200:
                return httpx.Response(self.balance_status, json={"name": "SERVICE_UNAVAILABLE"})
            refreshed = timestamp(self.clock.now() - timedelta(minutes=10))
            body = balance_body(self.available, self.withheld, refreshed=refreshed)
            return httpx.Response(200, json=body)
        if path == "/v1/reporting/transactions":
            self.searches.append(request)
            if self.search_status != 200:
                return httpx.Response(self.search_status, json={"name": "INTERNAL_SERVER_ERROR"})
            details = []
            if self.visible_on is not None and len(self.searches) >= self.visible_on:
                info = {
                    "transaction_id": self.capture_id,
                    "transaction_event_code": "T0006",
                    "transaction_initiation_date": at(-31),
                    "transaction_updated_date": at(-31),
                    "transaction_status": "S",
                }
                details.append({"transaction_info": info, "payer_info": {"email": BUYER}})
            return httpx.Response(
                200,
                json={
                    "transaction_details": details,
                    "last_refreshed_datetime": timestamp(self.clock.now()),
                },
            )
        return httpx.Response(404, json={"name": "RESOURCE_NOT_FOUND"})


@pytest.fixture
def settings():
    return SandboxSettings(client_id=CLIENT_ID, client_secret=SECRET, buyer_email=BUYER)


@pytest.fixture
def root(tmp_path):
    seed(tmp_path, "D1", {"verdict": "CONFIRMED"})
    return tmp_path


class Harness:
    def __init__(self, settings, root, fake, clock):
        self.settings, self.root, self.fake, self.clock = settings, root, fake, clock
        self.lines: list[str] = []

    async def run(self):
        runner = Runner(
            self.settings,
            root=self.root,
            transport=httpx.MockTransport(self.fake.handle),
            clock=self.clock.now,
            output=self.lines.append,
            manual=ManualSteps(
                Redactor.from_settings(self.settings),
                input_fn=lambda _: "",  # Enter at "D10 has finished"
                output_fn=self.lines.append,
            ),
            sleep=self.clock.sleep,
        )
        return await runner.run("D12")

    def findings(self) -> dict:
        path = self.root / "2026-10-24" / "D12" / "findings.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def assert_no_private_values(self):
        files = "".join(
            path.read_text(encoding="utf-8") for path in self.root.rglob("*") if path.is_file()
        )
        text = files + "\n".join(self.lines)
        for private in (CLIENT_ID, SECRET, TOKEN, BUYER):
            assert private not in text


def happy_readings(**changes):
    """Every 5 minutes from 120 to 5 minutes ago: the dispute hold of $60 lands 15 minutes
    after it was opened, the $400 capture (net $385) 20 minutes after it completed."""
    rows = []
    for minutes in range(-120, 0, 5):
        held = 6000 if minutes >= -75 else 0
        captured = 38500 if minutes >= -10 else 0
        rows.append(reading(minutes, 100000 - held + captured, held, **changes))
    return rows


def seed_happy(root):
    seed(
        root,
        "D2",
        {"timeline": [event("D2", "CAPTURE_COMPLETED", "CAP400", -30, 40000)]},
    )
    seed(
        root,
        "D3",
        {"timeline": [event("D3", "PAYOUT_ITEM_SUCCESS", "ITEM1", -40)]},  # no balance effect
    )
    snapshot = {
        "fetched_at": at(-60),
        "status": 200,
        "available": "940.00",
        "withheld": "60.00",
        "total": "1000.00",
        "last_refresh_time": at(-70),
        "as_of_time": at(-70),
    }
    seed(
        root,
        "D8",
        {
            "baseline_balance": {
                **snapshot,
                "fetched_at": at(-100),
                "available": "1000.00",
                "withheld": "0.00",
                "last_refresh_time": at(-110),
                "as_of_time": at(-110),
            },
            "hold_observations": [{"at": at(-60), "balance": snapshot, "captures": {}}],
            "timeline": [event("D8", "DISPUTE_CREATED", "PP-D-0001", -90, 6000)],
        },
    )


@pytest.mark.asyncio
async def test_fresh_balances_reflect_events_and_the_capture_appears_on_the_third_search(
    settings, root
):
    seed_happy(root)
    write_readings(root, happy_readings())
    clock = FakeClock()
    fake = FakePayPal(clock, available=132500, withheld=6000)
    harness = Harness(settings, root, fake, clock)

    state = await harness.run()

    found = harness.findings()
    assert found["verdict"] == "CONFIRMED"
    assert found["part_verdicts"] == {
        "freshness": "CONFIRMED",
        "consistency": "CONFIRMED",
        "reflection": "CONFIRMED",
        "transactions": "CONFIRMED",
    }
    # 24 synthetic lines, the runner's poller line written while D12 waits for D10,
    # D12's fresh read and two D8 snapshots.
    assert found["readings"]["valid"] == 28
    assert found["readings"]["by_source"] == {
        "balances.jsonl": 25,
        "D12.fresh_balance": 1,
        "D8.baseline_balance": 1,
        "D8.hold_observations": 1,
    }
    refresh = found["freshness"]["last_refresh_time"]
    assert refresh["count"] == 28
    assert refresh["min_seconds"] == refresh["median_seconds"] == 600
    assert found["freshness"]["any_over_3h"] is False
    assert found["consistency"]["checked"] == 28
    assert found["consistency"]["mismatches"] == 0

    rows = {row["event"]: row for row in found["reflection"]["rows"]}
    assert set(rows) == {"DISPUTE_CREATED", "CAPTURE_COMPLETED"}  # PAYOUT_ITEM_SUCCESS skipped
    hold, capture = rows["DISPUTE_CREATED"], rows["CAPTURE_COMPLETED"]
    assert hold["result"] == "REFLECTED"
    assert hold["lag_seconds"] == 900
    assert hold["lag_lower_bound_seconds"] == 600
    assert hold["reflected_in"]["change_minor"] == 6000
    assert capture["result"] == "REFLECTED"
    assert capture["lag_seconds"] == 1200
    assert capture["reflected_in"]["change_minor"] == 38500  # net of fees, above half of gross
    assert capture["threshold_minor"] == 20000
    assert hold["overlapping_events"] == capture["overlapping_events"] == []

    txn = found["transactions"]
    assert txn["first_seen_poll"] == 3
    assert txn["seconds_after_event"] == 2400  # NOW + 10 minutes, capture 30 minutes before
    assert txn["not_seen_until_seconds"] == 2100
    assert txn["upper_bound"] is False
    assert clock.sleeps == [300, 300]
    first = fake.searches[0].url.params
    assert first["transaction_id"] == "CAP400"
    assert first["start_date"] == "2026-10-23T08:30:00Z"
    assert first["end_date"] == "2026-10-24T09:00:00Z"
    assert first["fields"] == "transaction_info"
    assert state["completed_steps"] == [
        "fresh_balance",
        "poll_transactions",
        "wait_for_settlement",
        "findings",
    ]
    assert any("once D10 has finished" in line for line in harness.lines)
    harness.assert_no_private_values()


@pytest.mark.asyncio
async def test_stale_refresh_times_are_different(settings, root):
    seed_happy(root)
    write_readings(root, happy_readings(lag=200))
    clock = FakeClock()
    harness = Harness(settings, root, FakePayPal(clock, available=132500, withheld=6000), clock)

    await harness.run()

    found = harness.findings()
    assert found["verdict"] == "DIFFERENT"
    assert found["part_verdicts"]["freshness"] == "DIFFERENT"
    assert found["freshness"]["any_over_3h"] is True
    assert found["freshness"]["last_refresh_time"]["over_3h"] == 24
    assert found["freshness"]["as_of_time"]["max_seconds"] == 12000
    assert found["freshness"]["stale_examples"][0]["lag_seconds"] == 12000
    assert found["part_verdicts"]["reflection"] == "CONFIRMED"
    harness.assert_no_private_values()


@pytest.mark.asyncio
async def test_available_plus_withheld_not_total_is_different(settings, root):
    seed_happy(root)
    rows = happy_readings()
    rows[3] = reading(-105, 100000, 0, total=99900)
    write_readings(root, rows)
    clock = FakeClock()
    harness = Harness(settings, root, FakePayPal(clock, available=132500, withheld=6000), clock)

    await harness.run()

    found = harness.findings()
    assert found["verdict"] == "DIFFERENT"
    assert found["consistency"]["mismatches"] == 1
    example = found["consistency"]["mismatch_examples"][0]
    assert example["source"] == "balances.jsonl:4"
    assert example["difference_minor"] == 100
    assert found["part_verdicts"]["freshness"] == "CONFIRMED"
    harness.assert_no_private_values()


@pytest.mark.asyncio
async def test_an_event_never_reflected_over_three_hours_is_different(settings, root):
    seed(root, "D3", {"timeline": [event("D3", "PAYOUT_SUBMITTED", "BATCH1", -240, 1000)]})
    write_readings(root, [reading(minutes, 100000) for minutes in range(-300, 0, 5)])
    clock = FakeClock()
    fake = FakePayPal(clock, available=100000)
    harness = Harness(settings, root, fake, clock)

    await harness.run()

    found = harness.findings()
    (row,) = found["reflection"]["rows"]
    assert row["result"] == "NOT_REFLECTED"
    assert row["observed_until_seconds"] == 14400  # D12's fresh read at NOW
    assert found["verdict"] == "DIFFERENT"
    # No capture in the timeline: the transactions part is blocked and never searched.
    assert found["transactions"]["verdict"] == "BLOCKED"
    assert fake.searches == []
    harness.assert_no_private_values()


@pytest.mark.parametrize(
    ("event_minutes", "rows", "balance_status", "reason"),
    [
        (  # every read around the event failed; later reads are fine
            -240,
            [reading(m, status=None) for m in range(-300, -180, 5)]
            + [reading(m, status=503) for m in range(-180, -170, 5)]
            + [reading(m, 100000) for m in range(-170, 0, 5)],
            200,
            "no valid withheld USD reading at or before the event",
        ),
        (  # a baseline exists, but every read after the event failed
            -240,
            [reading(-250, 100000)] + [reading(m, status=None) for m in range(-235, 0, 5)],
            503,
            "no valid reading after the event",
        ),
        (  # the event is only 60 minutes old: too early to call it not reflected
            -60,
            [reading(m, 100000) for m in range(-300, 0, 5)],
            200,
            "valid readings end 3600.0 s after the event, within 3 hours",
        ),
    ],
)
@pytest.mark.asyncio
async def test_missing_readings_around_an_event_are_blocked_not_unreflected(
    settings, root, event_minutes, rows, balance_status, reason
):
    created = event("D8", "DISPUTE_CREATED", "PP-D-0001", event_minutes, 6000)
    seed(root, "D8", {"timeline": [created]})
    write_readings(root, rows)
    clock = FakeClock()
    fake = FakePayPal(clock, available=100000)
    fake.balance_status = balance_status
    harness = Harness(settings, root, fake, clock)

    await harness.run()

    found = harness.findings()
    (row,) = found["reflection"]["rows"]
    assert row["result"] == "BLOCKED"
    assert row["reason"] == reason
    assert found["part_verdicts"]["reflection"] == "BLOCKED"
    assert found["verdict"] != "DIFFERENT"
    harness.assert_no_private_values()


@pytest.mark.asyncio
async def test_overlapping_opposite_events_are_flagged_and_blocked(settings, root):
    seed(
        root,
        "D8",
        {"timeline": [event("D8", "DISPUTE_CREATED", "PP-D-0001", -240, 6000)]},
    )
    seed(
        root,
        "D10",
        {"timeline": [event("D10", "DISPUTE_RESOLVED", "PP-D-0001", -233, 6000)]},
    )
    write_readings(root, [reading(m, 100000) for m in range(-300, 0, 5)])
    clock = FakeClock()
    harness = Harness(settings, root, FakePayPal(clock, available=100000), clock)

    await harness.run()

    found = harness.findings()
    created, resolved = found["reflection"]["rows"]
    assert created["overlapping_events"] == ["DISPUTE_RESOLVED:PP-D-0001"]
    assert created["result"] == resolved["result"] == "BLOCKED"
    assert resolved["possible_cancelling_events"] == ["DISPUTE_CREATED:PP-D-0001"]
    assert found["reflection"]["overlapping"] == 1
    harness.assert_no_private_values()


@pytest.mark.parametrize(
    ("capture_minutes", "visible_on", "search_status", "verdict", "upper_bound"),
    [
        (-30, 1, 200, "CONFIRMED", True),  # already visible: lag is at most 30 minutes
        (-300, None, 200, "DIFFERENT", False),  # still missing more than 3 hours later
        (-1440, 1, 200, "BLOCKED", True),  # first seen a day later: not pinned down
        (-30, 1, 500, "BLOCKED", False),  # every search failed: no evidence
    ],
)
@pytest.mark.asyncio
async def test_transaction_search_lag_verdicts(
    settings, root, capture_minutes, visible_on, search_status, verdict, upper_bound
):
    seed(root, "D2", {"timeline": [event("D2", "CAPTURE_COMPLETED", "CAP400", capture_minutes)]})
    clock = FakeClock()
    fake = FakePayPal(clock, available=100000)
    fake.visible_on, fake.search_status = visible_on, search_status
    harness = Harness(settings, root, fake, clock)

    await harness.run()

    txn = harness.findings()["transactions"]
    assert txn["verdict"] == verdict
    assert txn["upper_bound"] is upper_bound
    expected_polls = 1 if visible_on and search_status == 200 else 37
    assert txn["polls"] == len(fake.searches) == expected_polls
    for request in fake.searches:
        start = datetime.fromisoformat(request.url.params["start_date"])
        end = datetime.fromisoformat(request.url.params["end_date"])
        assert timedelta(0) < end - start <= timedelta(days=31)
    harness.assert_no_private_values()


@pytest.mark.asyncio
async def test_interrupted_search_polling_resumes_without_repeating_work(settings, root):
    seed_happy(root)
    write_readings(root, happy_readings())
    clock = FakeClock()
    clock.interrupt_on_sleep = 2
    fake = FakePayPal(clock, available=132500, withheld=6000)
    harness = Harness(settings, root, fake, clock)

    with pytest.raises(CheckFailed):
        await harness.run()
    saved = json.loads((root / "2026-10-24" / "D12" / "state.json").read_text(encoding="utf-8"))
    assert saved["completed_steps"] == ["fresh_balance"]
    assert len(saved["data"]["transaction_polls"]) == 2

    state = await harness.run()

    assert len(fake.searches) == 3
    assert [poll["found"] for poll in state["data"]["transaction_polls"]] == [False, False, True]
    fresh_reads = [r for r in fake.balance_reads if r.url.params.get("currency_code") == "USD"]
    assert len(fresh_reads) == 1  # the fresh reading is not retaken on resume
    assert harness.findings()["transactions"]["first_seen_poll"] == 3
    assert harness.findings()["verdict"] == "CONFIRMED"
    harness.assert_no_private_values()


@pytest.mark.asyncio
async def test_no_usable_readings_is_blocked(settings, root):
    clock = FakeClock()
    fake = FakePayPal(clock)
    fake.balance_status = 503
    harness = Harness(settings, root, fake, clock)

    await harness.run()

    found = harness.findings()
    assert found["verdict"] == "BLOCKED"
    assert found["readings"]["valid"] == 0
    assert found["readings"]["invalid"] == 2  # D12's fresh read and the runner's poller line
    assert set(found["blocked_parts"]) == {"freshness", "consistency", "reflection", "transactions"}
    harness.assert_no_private_values()


def test_polling_defaults_match_plan():
    assert d12.TXN_INTERVAL_SECONDS == 300
    assert d12.TXN_OBSERVE_SECONDS == d12.CLAIM_SECONDS == 3 * 3600
    assert d12.TXN_LOOKBACK + d12.TXN_MAX_RANGE <= timedelta(days=31)
