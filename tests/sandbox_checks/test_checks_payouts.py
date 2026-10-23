"""Stateful stub PayPal: no network, runtime .env, or real browser actions."""

import asyncio
import json
import re
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
import pytest

from scripts.sandbox_checks.checks import payout_common as common
from scripts.sandbox_checks.env import SandboxSettings
from scripts.sandbox_checks.manual import ManualSteps
from scripts.sandbox_checks.redact import Redactor
from scripts.sandbox_checks.runner import CheckFailed, Runner

NOW = datetime(2026, 10, 24, 9, 0, tzinfo=UTC)
CLIENT_ID, SECRET, TOKEN = "FAKE-PAYOUT-CLIENT", "FAKE-PAYOUT-SECRET", "FAKE-PAYOUT-TOKEN"
MAKER_A, MAKER_B = "a@fake.example", "b@fake.example"
TRACKING = "9400100000000000000001"


class FakeClock:
    def __init__(self):
        self.value = NOW
        self.sleeps = []

    def now(self):
        return self.value

    async def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.value += timedelta(seconds=seconds)


class FakePayPal:
    def __init__(self):
        self.requests = []
        self.orders = {}
        self.idempotency = {}
        self.available = 500000
        self.withheld = 0
        self.tracking = {}
        self.drop = None
        self.before_post = lambda request: None
        self.batches = {}
        self.items = {}
        self.batch_keys = {}

    def handle(self, request):
        self.requests.append(request)
        if request.method == "POST" and request.url.path != "/v1/oauth2/token":
            self.before_post(request)
        response = self.respond(request)
        if self.drop == (request.method, request.url.path):
            self.drop = None
            raise httpx.ReadTimeout("simulated lost response", request=request)
        return response

    def respond(self, request):
        path, method = request.url.path, request.method
        if path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": TOKEN, "expires_in": 3600})
        if path == "/v1/reporting/balances":
            amount = lambda value: {"value": common.dollars(value), "currency_code": "USD"}
            return httpx.Response(
                200,
                json={
                    "balances": [
                        {
                            "currency": "USD",
                            "available_balance": amount(self.available),
                            "withheld_balance": amount(self.withheld),
                            "total_balance": amount(self.available + self.withheld),
                        }
                    ],
                    "last_refresh_time": NOW.isoformat(),
                },
            )
        key = (path, request.headers.get("paypal-request-id"))
        if method == "POST" and key in self.idempotency:
            return httpx.Response(201, json=self.idempotency[key])
        if path == "/v2/checkout/orders" and method == "POST":
            body = json.loads(request.content)
            order_id = f"ORDER{len(self.orders) + 1}"
            order = {
                "id": order_id,
                "status": "CREATED",
                "purchase_units": body["purchase_units"],
                "links": [
                    {
                        "rel": "approve",
                        "href": f"https://www.sandbox.paypal.com/checkoutnow?token={order_id}",
                    }
                ],
            }
            self.orders[order_id] = order
            self.idempotency[key] = json.loads(json.dumps(order))
            return httpx.Response(201, json=order)
        match = re.fullmatch(r"/v2/checkout/orders/(ORDER\d+)(?:/(capture|track))?", path)
        if match:
            order_id, action = match.groups()
            order = self.orders[order_id]
            if method == "GET":
                return httpx.Response(200, json=order)
            if action == "capture":
                assert order["status"] == "APPROVED"
                unit = order["purchase_units"][0]
                amount = common.cents(unit["amount"]["value"])
                capture_id = "CAP" + order_id.removeprefix("ORDER")
                capture = {
                    "update_time": NOW.isoformat(),
                    "id": capture_id,
                    "status": "COMPLETED",
                    "seller_receivable_breakdown": {
                        "gross_amount": unit["amount"],
                        "paypal_fee": {"value": "1.00", "currency_code": "USD"},
                        "net_amount": {
                            "value": common.dollars(amount - 100),
                            "currency_code": "USD",
                        },
                    },
                }
                unit["payments"] = {"captures": [capture]}
                order["status"] = "COMPLETED"
                self.available += amount - 100
                self.idempotency[key] = json.loads(json.dumps(order))
                return httpx.Response(201, json=order)
            if action == "track":
                body = json.loads(request.content)
                self.tracking[body["capture_id"]] = {
                    **body,
                    "status": "SHIPPED",
                    "shipment_uploader": "MERCHANT",
                    "tracking_number_validated": False,
                }
                return httpx.Response(201, json={"id": "TRACK1"})
        if path == "/v1/shipping/trackers":
            item = self.tracking.get(request.url.params["transaction_id"])
            return httpx.Response(200, json={"trackers": [item] if item else []})
        if path == "/v1/payments/payouts" and method == "POST":
            body = json.loads(request.content)
            sender = body["sender_batch_header"]["sender_batch_id"]
            if sender in self.batch_keys:
                return httpx.Response(
                    400,
                    json={
                        "name": "DUPLICATE_BATCH_ID",
                        "links": [
                            {
                                "rel": "self",
                                "method": "GET",
                                "href": "https://api-m.sandbox.paypal.com/v1/payments/payouts/"
                                + self.batch_keys[sender],
                            }
                        ],
                    },
                )
            batch_id, item_id = f"BATCH{len(self.batches) + 1}", f"ITEM{len(self.items) + 1}"
            payout = body["items"][0]
            self.items[item_id] = {
                "payout_item_id": item_id,
                "payout_batch_id": batch_id,
                "payout_item": payout,
                "transaction_status": "PENDING",
            }
            response = {
                "batch_header": {
                    "payout_batch_id": batch_id,
                    "batch_status": "PENDING",
                    "sender_batch_header": body["sender_batch_header"],
                }
            }
            self.batches[batch_id] = {**response, "item_id": item_id, "reads": 0}
            self.batch_keys[sender] = batch_id
            self.idempotency[key] = response
            self.available -= common.cents(payout["amount"]["value"])
            return httpx.Response(201, json=response)
        match = re.fullmatch(r"/v1/payments/payouts/(BATCH\d+)", path)
        if match and method == "GET":
            batch = self.batches[match.group(1)]
            batch["reads"] += 1
            item = self.items[batch["item_id"]]
            if batch["reads"] >= 2 and item["transaction_status"] == "PENDING":
                item["transaction_status"] = (
                    "UNCLAIMED" if "unreg" in item["payout_item"]["receiver"] else "SUCCESS"
                )
                batch["batch_header"]["batch_status"] = "SUCCESS"
            return httpx.Response(
                200, json={"batch_header": batch["batch_header"], "items": [item], "total_items": 1}
            )
        match = re.fullmatch(r"/v1/payments/payouts-item/(ITEM\d+)", path)
        if match and method == "GET":
            return httpx.Response(200, json=self.items[match.group(1)])
        match = re.fullmatch(r"/v1/payments/payouts-item/(ITEM\d+)/cancel", path)
        if match and method == "POST":
            item = self.items[match.group(1)]
            if item["transaction_status"] != "UNCLAIMED":
                return httpx.Response(422, json={"name": "CANNOT_CANCEL_COMPLETED_PAYMENT"})
            item["transaction_status"] = "RETURNED"
            self.available += common.cents(item["payout_item"]["amount"]["value"])
            return httpx.Response(200, json=item)
        return httpx.Response(404, json={"name": "RESOURCE_NOT_FOUND"})

    def posts(self, path):
        return [r for r in self.requests if r.method == "POST" and r.url.path == path]


class Operator:
    def __init__(self, fake):
        self.fake = fake
        self.lines = []
        self.prompt = ""

    def output(self, line):
        self.lines.append(line)
        if "Do not enter credentials" not in line:
            self.prompt = line
        if "log in as buyer-1 and approve" in line:
            for order in self.fake.orders.values():
                if order["status"] == "CREATED":
                    order["status"] = "APPROVED"

    def input(self, _):
        if "Type yes or no" in self.prompt:
            return "no"
        if "carrier code" in self.prompt:
            return "USPS"
        if "tracking number" in self.prompt:
            return TRACKING
        return ""


@pytest.fixture
def settings():
    return SandboxSettings(
        client_id=CLIENT_ID,
        client_secret=SECRET,
        maker_a_email=MAKER_A,
        maker_b_email=MAKER_B,
        buyer_email="buyer@fake.example",
        unregistered_emails=tuple(f"unreg{index}@fake.example" for index in range(1, 4)),
    )


@pytest.fixture
def root(tmp_path):
    path = tmp_path / "2026-10-24/D1/state.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"data": {"verdict": "CONFIRMED"}}))
    return tmp_path


def make_runner(settings, root, fake, operator, clock):
    return Runner(
        settings,
        root=root,
        transport=httpx.MockTransport(fake.handle),
        clock=clock.now,
        sleep=clock.sleep,
        output=operator.output,
        manual=ManualSteps(
            Redactor.from_settings(settings), input_fn=operator.input, output_fn=operator.output
        ),
    )


def assert_clean(root, operator):
    text = "\n".join(path.read_text() for path in root.rglob("*") if path.is_file())
    text += "\n".join(operator.lines)
    for value in (CLIENT_ID, SECRET, TOKEN, MAKER_A, MAKER_B, "buyer@fake.example"):
        assert value not in text
    assert not re.search(r"unreg[123]@fake\.example", text)


def test_uuid7_layout_and_integer_money():
    identifier = common.uuid7(lambda: NOW)
    assert identifier.version == 7 and identifier.variant == "specified in RFC 4122"
    assert identifier.int >> 80 == int(NOW.timestamp()) * 1000
    assert common.dollars(40000) == "400.00"
    assert common.cents("1.005") is None
    assert common.cents("NaN") is None
    assert common.cents("Infinity") is None
    with pytest.raises(ValueError):
        common.dollars(1.1)


@pytest.mark.asyncio
async def test_d2_saves_exact_capture_tracking_keys_and_net_timeline(settings, root):
    fake, clock = FakePayPal(), FakeClock()
    operator = Operator(fake)
    state = await make_runner(settings, root, fake, operator, clock).run("D2")
    data = state["data"]
    assert data["verdict"] == "CONFIRMED"
    assert {k: v["capture_id"] for k, v in data["captures"].items()} == {
        "inr_400": "CAP1",
        "snad_60": "CAP2",
        "spare_25": "CAP3",
    }
    assert data["tracking"] == {"inr_400": {"carrier": "USPS", "tracking_number": TRACKING}}
    assert [e["amount_minor"] for e in data["timeline"]] == [39900, 5900, 2400]
    assert all(e["event"] == "CAPTURE_COMPLETED" for e in data["timeline"])
    assert all(e["paypal_time"] == NOW.isoformat() for e in data["timeline"])
    assert all(
        r.headers.get("prefer") == "return=representation"
        for r in fake.requests
        if r.method == "POST" and r.url.path.startswith("/v2/checkout/orders")
    )
    assert len(fake.posts("/v2/checkout/orders")) == 3
    assert data["balance"]["first_reflected_at"]
    assert data["tracking_read"]["trackers"][0]["tracking_number_validated"] is False
    assert_clean(root, operator)


@pytest.mark.asyncio
async def test_d2_unknown_create_resumes_identically_after_durable_checkpoint(settings, root):
    fake, clock = FakePayPal(), FakeClock()
    operator = Operator(fake)
    fake.drop = ("POST", "/v2/checkout/orders")

    def inspect_saved(request):
        state = json.loads((root / "2026-10-24/D2/state.json").read_text())
        saved = next(
            w
            for w in state["data"]["writes"].values()
            if w["request_id"] == request.headers["paypal-request-id"]
        )
        assert saved["state"] == "UNKNOWN"
        assert saved["body_sha256"] == common.digest(json.loads(request.content))
        key = request.headers["paypal-request-id"]
        assert re.fullmatch(r"cs-[a-f0-9-]{36}-a1", key)
        assert UUID(key[3:-3]).version == 7

    fake.before_post = inspect_saved
    with pytest.raises(CheckFailed):
        await make_runner(settings, root, fake, operator, clock).run("D2")
    state = await make_runner(settings, root, fake, operator, clock).run("D2")
    first, replay = fake.posts("/v2/checkout/orders")[:2]
    assert first.content == replay.content
    assert first.headers["paypal-request-id"] == replay.headers["paypal-request-id"]
    assert len(fake.orders) == 3
    assert state["data"]["verdict"] == "CONFIRMED"
    assert_clean(root, operator)


@pytest.mark.asyncio
async def test_d2_lost_capture_is_recovered_by_get_without_a_second_post(settings, root):
    fake, clock = FakePayPal(), FakeClock()
    operator = Operator(fake)
    fake.drop = ("POST", "/v2/checkout/orders/ORDER1/capture")
    with pytest.raises(CheckFailed):
        await make_runner(settings, root, fake, operator, clock).run("D2")
    state = await make_runner(settings, root, fake, operator, clock).run("D2")
    assert len(fake.posts("/v2/checkout/orders/ORDER1/capture")) == 1
    assert len(state["data"]["timeline"]) == 3


@pytest.mark.asyncio
async def test_d2_unknown_tracking_does_not_send_twice(settings, root):
    fake, clock = FakePayPal(), FakeClock()
    operator = Operator(fake)
    fake.drop = ("POST", "/v2/checkout/orders/ORDER1/track")
    state = await make_runner(settings, root, fake, operator, clock).run("D2")
    assert state["data"]["verdict"] == "BLOCKED"
    assert len(fake.posts("/v2/checkout/orders/ORDER1/track")) == 1


@pytest.mark.asyncio
async def test_missing_balance_values_are_never_reported_as_a_change(settings, root, monkeypatch):
    fake, clock = FakePayPal(), FakeClock()
    operator = Operator(fake)
    original = fake.respond

    def unavailable(request):
        if request.url.path == "/v1/reporting/balances":
            return httpx.Response(503, json={"name": "SERVICE_UNAVAILABLE"})
        return original(request)

    fake.respond = unavailable
    monkeypatch.setattr(common, "BALANCE_POLLS", 2)
    state = await make_runner(settings, root, fake, operator, clock).run("D2")
    assert state["data"]["balance"]["first_reflected_at"] is None


@pytest.mark.asyncio
async def test_d2_completed_run_makes_no_network_calls(settings, root):
    fake, clock = FakePayPal(), FakeClock()
    operator = Operator(fake)
    await make_runner(settings, root, fake, operator, clock).run("D2")
    count = len(fake.requests)
    await make_runner(settings, root, fake, operator, clock).run("D2")
    assert len(fake.requests) == count


@pytest.mark.asyncio
async def test_d3_records_create_batch_poll_item_and_balance(settings, root):
    fake, clock = FakePayPal(), FakeClock()
    operator = Operator(fake)
    state = await make_runner(settings, root, fake, operator, clock).run("D3")
    data = state["data"]
    assert data["verdict"] == "CONFIRMED"
    assert data["payout"]["item_status"] == "SUCCESS"
    assert data["create_had_item_ids"] is False
    assert [h["status"] for h in data["payout"]["history"]] == ["PENDING", "SUCCESS"]
    assert [e["event"] for e in data["timeline"]] == ["PAYOUT_SUBMITTED", "PAYOUT_ITEM_SUCCESS"]
    assert data["balance"]["first_reflected_at"]
    assert len(fake.batches) == 1
    assert_clean(root, operator)


@pytest.mark.asyncio
async def test_d3_lost_create_restores_aliases_and_reuses_exact_body_and_keys(settings, root):
    fake, clock = FakePayPal(), FakeClock()
    operator = Operator(fake)
    fake.drop = ("POST", "/v1/payments/payouts")
    with pytest.raises(CheckFailed):
        await make_runner(settings, root, fake, operator, clock).run("D3")
    state = await make_runner(settings, root, fake, operator, clock).run("D3")
    first, replay = fake.posts("/v1/payments/payouts")
    assert first.content == replay.content
    assert first.headers["paypal-request-id"] == replay.headers["paypal-request-id"]
    assert len(fake.batches) == 1 and state["data"]["verdict"] == "CONFIRMED"
    assert_clean(root, operator)


@pytest.mark.asyncio
async def test_changed_payee_settings_refuse_resumed_write(settings, root):
    from dataclasses import replace

    fake, clock = FakePayPal(), FakeClock()
    operator = Operator(fake)
    fake.drop = ("POST", "/v1/payments/payouts")
    with pytest.raises(CheckFailed):
        await make_runner(settings, root, fake, operator, clock).run("D3")
    changed = replace(settings, maker_a_email="changed@fake.example")
    with pytest.raises(CheckFailed):
        await make_runner(changed, root, fake, operator, clock).run("D3")
    assert len(fake.posts("/v1/payments/payouts")) == 1


@pytest.mark.asyncio
async def test_linked_duplicate_batch_is_not_adopted_without_matching_payee(settings, root):
    fake, clock = FakePayPal(), FakeClock()
    operator = Operator(fake)
    fake.drop = ("POST", "/v1/payments/payouts")
    with pytest.raises(CheckFailed):
        await make_runner(settings, root, fake, operator, clock).run("D3")
    fake.idempotency.clear()  # model a duplicate-batch error rather than a cached 201
    fake.items["ITEM1"]["payout_item"]["receiver"] = MAKER_B
    with pytest.raises(CheckFailed):
        await make_runner(settings, root, fake, operator, clock).run("D3")
    assert len(fake.batches) == 1


@pytest.mark.asyncio
async def test_d4_all_three_unclaimed_and_balance_effects_are_recorded(settings, root):
    fake, clock = FakePayPal(), FakeClock()
    operator = Operator(fake)
    state = await make_runner(settings, root, fake, operator, clock).run("D4")
    data = state["data"]
    assert data["verdict"] == data["unclaimed_verdict"] == "CONFIRMED"
    assert set(data["payouts"]) == {"unreg_1", "unreg_2", "unreg_3"}
    assert all(p["item_status"] == "UNCLAIMED" for p in data["payouts"].values())
    assert data["balance_effect"]["available_delta_minor"] == -300
    assert data["balance_effect"]["withheld_delta_minor"] == 0
    assert sum(e["event"] == "PAYOUT_ITEM_UNCLAIMED" for e in data["timeline"]) == 3
    assert len(fake.batches) == 3
    assert_clean(root, operator)


@pytest.mark.asyncio
async def test_d4_resume_does_not_recreate_the_other_payouts(settings, root):
    fake, clock = FakePayPal(), FakeClock()
    operator = Operator(fake)
    original = fake.respond

    def drop_second(request):
        result = original(request)
        if (
            request.method == "POST"
            and request.url.path == "/v1/payments/payouts"
            and len(fake.batches) == 2
        ):
            fake.respond = original
            raise httpx.ReadTimeout("simulated lost second response")
        return result

    fake.respond = drop_second
    with pytest.raises(CheckFailed):
        await make_runner(settings, root, fake, operator, clock).run("D4")
    state = await make_runner(settings, root, fake, operator, clock).run("D4")
    assert len(fake.batches) == 3
    assert len(fake.posts("/v1/payments/payouts")) == 4
    assert state["data"]["verdict"] == "CONFIRMED"
    assert_clean(root, operator)


@pytest.mark.asyncio
async def test_d4_missing_addresses_blocks_before_any_payout(settings, root):
    from dataclasses import replace

    fake, clock = FakePayPal(), FakeClock()
    operator = Operator(fake)
    state = await make_runner(
        replace(settings, unregistered_emails=()), root, fake, operator, clock
    ).run("D4")
    assert state["data"]["verdict"] == "BLOCKED"
    assert not fake.posts("/v1/payments/payouts")


async def prerequisites(settings, root, fake, operator, clock):
    states = {}
    for check in ("D3", "D4"):
        states[check] = await make_runner(settings, root, fake, operator, clock).run(check)
    return states


@pytest.mark.asyncio
async def test_d5_cancel_get_negatives_and_uncancelled_monitor(settings, root, monkeypatch):
    from scripts.sandbox_checks.checks import d05_cancel as d5

    fake, clock = FakePayPal(), FakeClock()
    operator = Operator(fake)
    prior = await prerequisites(settings, root, fake, operator, clock)
    monkeypatch.setattr(d5, "MONITOR_DEADLINE", clock.now() + timedelta(minutes=10))
    state = await make_runner(settings, root, fake, operator, clock).run("D5")
    data = state["data"]
    assert data["verdict"] == "CONFIRMED"
    assert data["cancel"]["response_transaction_status"] == "RETURNED"
    assert data["cancel"]["final"]["transaction_status"] == "RETURNED"
    assert set(data["negative_cases"]) == {"cancel_again", "cancel_success"}
    assert all(case["status"] == 422 for case in data["negative_cases"].values())
    assert data["balance"]["first_reflected_at"]
    control = prior["D4"]["data"]["payouts"]["unreg_2"]["item_id"]
    assert not fake.posts(f"/v1/payments/payouts-item/{control}/cancel")
    assert len(data["control_observations"]) >= 3
    assert all(s["status"] == "UNCLAIMED" for s in data["control_observations"])
    assert any(e["event"] == "PAYOUT_ITEM_RETURNED" for e in data["timeline"])
    assert_clean(root, operator)


@pytest.mark.asyncio
async def test_d5_lost_cancel_is_get_reconciled_and_only_negative_case_resends(
    settings, root, monkeypatch
):
    from scripts.sandbox_checks.checks import d05_cancel as d5

    fake, clock = FakePayPal(), FakeClock()
    operator = Operator(fake)
    prior = await prerequisites(settings, root, fake, operator, clock)
    item_id = prior["D4"]["data"]["payouts"]["unreg_1"]["item_id"]
    fake.drop = ("POST", f"/v1/payments/payouts-item/{item_id}/cancel")
    monkeypatch.setattr(d5, "MONITOR_DEADLINE", clock.now())
    state = await make_runner(settings, root, fake, operator, clock).run("D5")
    assert state["data"]["cancel"]["write_state"] == "UNKNOWN"
    assert state["data"]["cancel"]["final"]["transaction_status"] == "RETURNED"
    assert len(fake.posts(f"/v1/payments/payouts-item/{item_id}/cancel")) == 2
    assert state["data"]["negative_cases"]["cancel_again"]["status"] == 422


@pytest.mark.asyncio
async def test_d5_does_not_cancel_without_prerequisites(settings, root):
    fake, clock = FakePayPal(), FakeClock()
    operator = Operator(fake)
    state = await make_runner(settings, root, fake, operator, clock).run("D5")
    assert state["data"]["verdict"] == "BLOCKED"
    assert not [r for r in fake.requests if r.url.path.endswith("/cancel")]


def lose_short_timeout_response(fake):
    original = fake.respond
    dropped = []

    def respond(request):
        result = original(request)
        if (
            request.url.path == "/v1/payments/payouts"
            and request.method == "POST"
            and request.extensions["timeout"]["read"] == 0.05
            and not dropped
        ):
            dropped.append(request)
            raise httpx.ReadTimeout("simulated accepted payout with lost response", request=request)
        return result

    fake.respond = respond
    return dropped


@pytest.mark.asyncio
async def test_d6_all_experiments_checkpoint_keys_and_confirm_one_item(settings, root):
    fake, clock = FakePayPal(), FakeClock()
    operator = Operator(fake)
    dropped = lose_short_timeout_response(fake)

    def inspect_saved(request):
        state = json.loads((root / "2026-10-24/D6/state.json").read_text())
        matches = [
            entry
            for entry in state["data"]["writes"].values()
            if entry["request_id"] == request.headers["paypal-request-id"]
            and entry["body_sha256"] == common.digest(json.loads(request.content))
            and entry["state"] == "UNKNOWN"
        ]
        assert matches, "The exact request must be checkpointed before POST"

    fake.before_post = inspect_saved
    state = await make_runner(settings, root, fake, operator, clock).run("D6")
    data = state["data"]
    assert data["verdict"] == "CONFIRMED"
    experiments = data["experiments"]
    assert set(experiments) == set("abcde")
    assert experiments["a"]["same_batch"]
    assert experiments["b"]["link_points_to_original"]
    assert experiments["b"]["error_name"] == "DUPLICATE_BATCH_ID"
    assert experiments["c"]["winning_body"] == "original"
    assert len(experiments["d"]["responses"]) == 2
    assert experiments["e"]["initial_unknown"]
    assert experiments["e"]["initial"]["state"] == "UNKNOWN"
    assert experiments["e"]["balance"]["first_reflected_at"]
    assert len(dropped) == 1 and dropped[0].extensions["timeout"] == {
        "connect": 30,
        "read": 0.05,
        "write": 30,
        "pool": 30,
    }
    assert len(fake.batches) == len(fake.items) == 5
    assert len(fake.posts("/v1/payments/payouts")) == 10
    assert len(fake.batch_keys) == 5
    assert fake.available == 500000 - 500
    assert all(e["verified_single_item"] for e in experiments.values())
    assert_clean(root, operator)


@pytest.mark.asyncio
async def test_d6_pair_is_actually_concurrent(settings, root):
    fake, clock = FakePayPal(), FakeClock()
    operator = Operator(fake)
    lose_short_timeout_response(fake)
    arrived, concurrent_requests = asyncio.Event(), []

    async def handle(request):
        if request.url.path == "/v1/payments/payouts" and request.method == "POST":
            state = json.loads((root / "2026-10-24/D6/state.json").read_text())
            concurrent = (state["data"].get("writes") or {}).get("d_concurrent")
            if concurrent and concurrent["request_id"] == request.headers["paypal-request-id"]:
                concurrent_requests.append(request)
                if len(concurrent_requests) == 2:
                    arrived.set()
                await asyncio.wait_for(arrived.wait(), timeout=2)
        return fake.handle(request)

    runner = make_runner(settings, root, fake, operator, clock)
    runner.transport = httpx.MockTransport(handle)
    await runner.run("D6")
    assert len(concurrent_requests) == 2
    first, second = concurrent_requests
    assert first.content == second.content
    assert first.headers["paypal-request-id"] == second.headers["paypal-request-id"]


@pytest.mark.asyncio
async def test_d6_does_not_claim_a_lost_response_if_the_call_was_fast(settings, root):
    fake, clock = FakePayPal(), FakeClock()
    operator = Operator(fake)
    state = await make_runner(settings, root, fake, operator, clock).run("D6")
    assert state["data"]["verdict"] == "BLOCKED"
    assert state["data"]["experiments"]["e"]["initial_unknown"] is False


@pytest.mark.asyncio
async def test_d6_interrupted_lost_response_recovers_without_new_keys(settings, root):
    fake, clock = FakePayPal(), FakeClock()
    operator = Operator(fake)
    original = fake.respond
    failed = []

    def accepted_then_interrupted(request):
        response = original(request)
        if (
            request.url.path == "/v1/payments/payouts"
            and request.method == "POST"
            and request.extensions["timeout"]["read"] == 0.05
            and not failed
        ):
            failed.append(request)
            raise RuntimeError("simulated process interruption after acceptance")
        return response

    fake.respond = accepted_then_interrupted
    with pytest.raises(CheckFailed):
        await make_runner(settings, root, fake, operator, clock).run("D6")
    state = await make_runner(settings, root, fake, operator, clock).run("D6")
    assert len(fake.batches) == 5
    assert state["data"]["experiments"]["e"]["initial_unknown"]
    original_request = failed[0]
    replays = [
        r
        for r in fake.posts("/v1/payments/payouts")
        if r.headers["paypal-request-id"] == original_request.headers["paypal-request-id"]
    ]
    assert len(replays) == 2
    assert all(r.content == original_request.content for r in replays)
    assert_clean(root, operator)


@pytest.mark.asyncio
async def test_total_items_cannot_hide_a_second_item_on_another_page(settings, root):
    fake, clock = FakePayPal(), FakeClock()
    operator = Operator(fake)
    original = fake.respond

    def hidden_second_item(request):
        response = original(request)
        if request.method == "GET" and request.url.path.startswith("/v1/payments/payouts/"):
            return httpx.Response(200, json={**response.json(), "total_items": 2})
        return response

    fake.respond = hidden_second_item
    with pytest.raises(CheckFailed):
        await make_runner(settings, root, fake, operator, clock).run("D3")


@pytest.mark.asyncio
async def test_capture_webhook_delay_is_correlated_without_matching_missing_ids(settings, root):
    fake, clock = FakePayPal(), FakeClock()
    operator = Operator(fake)
    directory = root / "webhooks"
    directory.mkdir()
    for name, capture in (("matching", "CAP1"), ("unrelated", "SOMETHING_ELSE")):
        (directory / f"{name}.json").write_text(
            json.dumps(
                {
                    "event_id": name,
                    "event_type": "PAYMENT.CAPTURE.COMPLETED",
                    "create_time": NOW.isoformat(),
                    "received_at": (NOW + timedelta(seconds=2)).isoformat(),
                    "body": {"resource": {"id": capture}},
                }
            )
        )
    state = await make_runner(settings, root, fake, operator, clock).run("D2")
    assert [event["event_id"] for event in state["data"]["webhooks"]] == ["matching"]
    assert state["data"]["webhooks"][0]["delay_seconds"] == 2
    from types import SimpleNamespace

    assert [
        event["event_id"] for event in common.webhooks(SimpleNamespace(root=root), ["CAP1", None])
    ] == ["matching"]
    assert common.webhooks(SimpleNamespace(root=root), [None]) == []
