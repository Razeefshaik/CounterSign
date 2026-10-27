"""C21 flow through public facade calls; test SQL is observation/setup only."""

import gzip
import json
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from countersign.api import owner as owner_api
from countersign.api.owner import build_router
from countersign.approvals import ApprovalService
from countersign.config import PayPalSettings, Settings
from countersign.db import Database, Repositories
from countersign.harness import Engine, EngineSettings
from countersign.paypal.http import PayPalHTTP
from mock_paypal.app import create_app
from scripts.sandbox_checks.client import SandboxClient
from scripts.sandbox_checks.env import SandboxSettings
from scripts.sandbox_checks.manual import ManualSteps
from scripts.sandbox_checks.redact import Redactor
from scripts.slice_e2e import (
    Checkpoint,
    ExchangeRecorder,
    LostResponse,
    RecoveryGate,
    ResourceLog,
    SliceError,
    check_webhook,
    drive,
    payout_week,
    sandbox_orders,
    seed_state,
    serve_cli,
    settled_slice,
    webhook_agrees,
)

NOW = datetime(2026, 11, 6, 9, tzinfo=UTC)


@pytest.mark.parametrize("day,week", [(6, "2026-11-02"), (7, "2026-11-09")])
def test_run_week_tracks_seed_next_friday_at_the_week_boundary(day, week):
    assert payout_week(datetime(2026, 11, day, tzinfo=UTC)) == date.fromisoformat(week)


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
    assert store.data["lost_response_discarded"] is True
    store.close()
    resumed = checkpoint(tmp_path)
    second = LostResponse(httpx.MockTransport(respond), resumed)
    response = await second.handle_async_request(request)
    await response.aclose()
    assert len(requests) == 2  # only the executor may decide to replay identical keys
    assert (tmp_path / "run.jsonl").read_text().count('"STAGED_LOST_RESPONSE"') == 1
    resumed.close()


@pytest.mark.asyncio
async def test_network_failure_is_not_reported_as_an_injected_lost_response(tmp_path):
    store = checkpoint(tmp_path)

    async def unavailable(request):
        raise httpx.ConnectError("fake unavailable", request=request)

    wrapper = LostResponse(httpx.MockTransport(unavailable), store)
    with pytest.raises(httpx.ConnectError):
        await wrapper.handle_async_request(
            httpx.Request("POST", "https://api-m.sandbox.paypal.com/v1/payments/payouts")
        )
    assert store.data["lost_response_armed"] is True
    assert not store.data.get("lost_response_discarded")
    store.close()


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


@asynccontextmanager
async def slice_world(
    db_schema, tmp_path, *, base_url="http://localhost:8099", driver_stages=False
):
    holder, delivered, replays, read_faults = {}, [], [], {}
    payout_posts, exchanges = [], []

    async def delivery(url, headers, body):
        status = await holder["engine"].deliver_webhook(headers, body)
        delivered.append((headers, body, status))
        return status

    mock = create_app(control_enabled=True, webhook_delivery=delivery)
    inner = httpx.ASGITransport(app=mock)
    store = checkpoint(tmp_path)
    base = base_url

    async def respond(request):
        payout_post = request.method == "POST" and request.url.path == "/v1/payments/payouts"
        if payout_post:
            payout_posts.append((request.headers["PayPal-Request-Id"], request.content))
        if request.method == "GET":
            exchanges.append(("GET", str(request.url)))
        response = await inner.handle_async_request(request)
        if payout_post and response.status_code == 400:
            # Observe M09a's actual response. Never synthesize a duplicate or
            # short-circuit the payout route that must enforce idempotency.
            await response.aread()
            body = response.json()
            assert body["name"] == "USER_BUSINESS_ERROR" and body["links"] == []
            href = body["details"][0]["link"][0]["href"]
            assert href.startswith(base + "/v1/payments/payouts/")
            replays.append(request)
            exchanges.append(("DUPLICATE_400", href))
        if (
            request.method == "GET"
            and request.url.path.startswith("/v1/payments/payouts-item/")
            and read_faults.get("status")
        ):
            await response.aread()
            body = response.json()
            body["transaction_status"] = read_faults["status"]
            return httpx.Response(200, json=body)
        return response

    gate = RecoveryGate(ResourceLog(httpx.MockTransport(respond), store), store)
    async with (
        Database(db_schema.conninfo) as database,
        httpx.AsyncClient(transport=inner, base_url=base) as control,
    ):
        assert (
            await control.post("/_mock/clock/set", json={"now": NOW.isoformat()})
        ).status_code == 200
        emails = {key: "maker-" + key.lower() + "@example.test" for key in ("A", "B")}
        orders = {
            key: {"order_id": "ORDER-" + key, "capture_id": "CAPTURE-" + key} for key in ("A", "B")
        }
        assert (
            await control.post(
                "/_mock/seed",
                json={
                    "balances": {"available": "100.00"},
                    "registered_receivers": list(emails.values()),
                    "captures": [
                        {"id": value["capture_id"], "order_id": value["order_id"], "amount": "2.00"}
                        for value in orders.values()
                    ],
                },
            )
        ).status_code == 200
        # Configure the mock's real signed deliveries; do not fabricate inbox rows.
        token = (
            await control.post(
                "/v1/oauth2/token",
                auth=("mock-client", "mock-secret"),
                data={"grant_type": "client_credentials"},
            )
        ).json()["access_token"]
        registered = await control.post(
            "/v1/notifications/webhooks",
            headers={"Authorization": "Bearer " + token},
            json={"url": "http://localhost:8000/webhooks/paypal", "event_types": [{"name": "*"}]},
        )
        assert registered.status_code == 201
        config = EngineSettings(
            database_url=db_schema.conninfo,
            paypal_base_url=base,
            paypal_client_id="fake-client",
            paypal_client_secret="fake-secret",
            webhook_id=registered.json()["id"],
            clock=lambda: mock.state.store.clock.now,
        )
        async with (
            PayPalHTTP(
                PayPalSettings(base, "fake-client", "fake-secret", config.webhook_id),
                transport=gate,
            ) as http,
            Engine(config, database=database, http=http) as engine,
        ):
            holder["engine"] = engine
            async with database.tx() as conn:
                assert (
                    await (await conn.execute("SELECT version_num FROM alembic_version")).fetchone()
                )["version_num"] == "0006"
                guards = await conn.execute(
                    "SELECT c.relname,t.tgenabled FROM pg_trigger t "
                    "JOIN pg_class c ON c.oid=t.tgrelid "
                    "WHERE c.relnamespace=current_schema()::regnamespace "
                    "AND t.tgname='guard_state_transition'"
                )
                installed = {row["relname"]: row["tgenabled"] for row in await guards.fetchall()}
                assert installed == dict.fromkeys(
                    (
                        "obligations",
                        "payout_lines",
                        "payout_attempts",
                        "operations",
                        "proposals",
                        "incidents",
                    ),
                    "O",
                )
            tick = engine.tick

            async def checked_tick():
                result = await tick()
                assert result["errors"] == 0, "slice tick must not hide a rejected transition"
                return result

            engine.tick = checked_tick
            await engine.seed(seed_state(emails, orders, NOW))
            store.checkpoint(week_start="2026-11-02", orders=orders)
            owner = FastAPI()
            with pytest.MonkeyPatch.context() as clock_patch:
                clock_patch.setattr(
                    owner_api,
                    "ApprovalService",
                    lambda db, **kw: ApprovalService(db, clock=config.clock, **kw),
                )
                owner.include_router(
                    build_router(database, Settings("postgresql://fake:fake@localhost/slice_test")),
                    prefix="/api/v1",
                )
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=owner), base_url="http://localhost:8000"
            ) as api:

                async def approve(proposal_id, payload_hash):
                    response = await api.post(
                        f"/api/v1/proposals/{proposal_id}/approve",
                        json={"payload_hash": payload_hash, "approved_by": "slice-test-owner"},
                    )
                    assert response.status_code == 201

                async def advance():
                    assert (
                        await control.post("/_mock/clock/advance", json={"seconds": 10})
                    ).status_code == 200

                if not driver_stages:
                    await stage_dispute_and_fault(control, advance, engine)
                yield SimpleNamespace(
                    engine=engine,
                    gate=gate,
                    checkpoint=store,
                    control=control,
                    database=database,
                    approve=approve,
                    advance=advance,
                    delivered=delivered,
                    replays=replays,
                    payout_posts=payout_posts,
                    exchanges=exchanges,
                    read_faults=read_faults,
                    mock=mock,
                    api=api,
                    http=http,
                )
    store.close()


async def stage_dispute_and_fault(control, advance, engine):
    result = await control.post(
        "/_mock/disputes",
        json={
            "capture_id": "CAPTURE-A",
            "amount": "2.00",
            "reason": "MERCHANDISE_OR_SERVICE_NOT_RECEIVED",
        },
    )
    assert result.status_code == 200
    await advance()
    await engine.tick()
    # The only payout fault sent to the mock is the requested after-commit drop.
    assert (
        await control.post(
            "/_mock/faults",
            json={
                "match": {
                    "method": "POST",
                    "path": "^/v1/payments/payouts$",
                    "nth": 1,
                    "count": 1,
                },
                "action": {"type": "drop_response", "after_commit": True},
            },
        )
    ).status_code == 201


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize("base_url", ["http://localhost:8099", "http://127.0.0.1:8099"])
async def test_vertical_slice_reserves_a_pays_b_once_and_reconciles(db_schema, tmp_path, base_url):
    async with slice_world(db_schema, tmp_path, base_url=base_url) as world:
        final = await settled_slice(
            world.engine, world.checkpoint, world.gate, world.approve, world.advance
        )
        # Drain genuine signed duplicate/batch deliveries through the inbox too.
        await world.advance()
        for _ in range(8):
            await world.engine.tick()
        truth = (await world.control.get("/_mock/ground-truth")).json()
        [item] = truth["payout_items"]
        assert item["receiver"] == "maker-b@example.test" and item["status"] == "SUCCESS"
        assert item["amount"] == {"value": "1.80", "currency": "USD"}
        assert len(world.replays) == 1
        assert len(world.payout_posts) == 2 and world.payout_posts[0] == world.payout_posts[1]
        href = base_url + "/v1/payments/payouts/" + item["payout_batch_id"]
        duplicate = world.exchanges.index(("DUPLICATE_400", href))
        assert ("GET", href) in world.exchanges[duplicate + 1 :]
        assert world.delivered and all(status == 200 for _, _, status in world.delivered)
        async with world.database.tx() as conn:
            repos = Repositories(conn)
            obligations = await repos.obligations.find()
            assert sorted(o["state"] for o in obligations) == ["RESERVED", "SETTLED"]
            [attempt] = await repos.payout_attempts.find()
            assert attempt["state"] == item["status"] == attempt["paypal_raw"]["transaction_status"]
            [operation] = await repos.operations.find()
            assert operation["state"] == "SUCCEEDED"
            [approval] = await repos.approvals.find()
            assert approval["approved_by"] == "slice-test-owner" and approval["used_at"] is not None
            journals = await repos.ledger_journals.find()
            assert sum(j["memo"].startswith("PAYOUT_SUCCESS:") for j in journals) == 1
            inbox = await repos.webhook_inbox.find(limit=1000)
            assert inbox and all(
                e["signature"] == "SUCCESS" and e["status"] in {"PROCESSED", "IGNORED"}
                for e in inbox
            )
            item_events = [e for e in inbox if e["event_type"] == "PAYMENT.PAYOUTS-ITEM.SUCCEEDED"]
            assert len(item_events) == 1 and item_events[0]["resource_id"] == item["payout_item_id"]
            audits = await repos.audit_log.find(limit=1000)
            closures = [
                a
                for a in audits
                if a["kind"] == "INCIDENT_RESOLVED"
                and a["details"]["incident_id"] == world.checkpoint.data["payout_incident_id"]
            ]
            assert len(closures) == 1 and closures[0]["details"]["reason"] == "RECONCILED"
        [dispute] = final["disputes"]
        assert dispute["phase"] == "ACTIVE" and dispute["reserved"]["minor"] == 180
        [case] = [i for i in final["incidents"] if i["kind"] == "DISPUTE"]
        assert case["state"] != "RESOLVED"
        assert await world.engine.pending_approvals() == []
        await webhook_agrees(world.engine, world.checkpoint, advance=world.advance)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_payout_incident_stays_open_until_get_agrees_then_resume_pays_nobody_twice(
    db_schema, tmp_path
):
    async with slice_world(db_schema, tmp_path) as world:
        # A signed SUCCESS webhook arrives, while the item GET still says PENDING.
        world.read_faults["status"] = "PENDING"
        with pytest.raises(SliceError, match="SLICE_PENDING_RECONCILIATION"):
            await settled_slice(
                world.engine,
                world.checkpoint,
                world.gate,
                world.approve,
                world.advance,
                poll_limit=6,
            )
        truth = (await world.control.get("/_mock/ground-truth")).json()
        assert len(truth["payout_items"]) == 1 and truth["payout_items"][0]["status"] == "SUCCESS"
        snapshot = await world.engine.snapshot()
        [case] = [i for i in snapshot["incidents"] if i["kind"] == "PAYOUT_ITEM"]
        assert case["state"] != "RESOLVED" and case["closed_at"] is None
        async with world.database.tx() as conn:
            repos = Repositories(conn)
            obligations = await repos.obligations.find()
            assert sorted(o["state"] for o in obligations) == ["IN_FLIGHT", "RESERVED"]
            inbox = await repos.webhook_inbox.find(limit=1000)
            assert any(
                e["event_type"] == "PAYMENT.PAYOUTS-ITEM.SUCCEEDED" and e["signature"] == "SUCCESS"
                for e in inbox
            )
            assert not any(
                j["memo"].startswith("PAYOUT_SUCCESS:") for j in await repos.ledger_journals.find()
            )
        world.read_faults.clear()

        async def no_second_approval(*_):
            pytest.fail("resume must not ask for or consume another approval")

        final = await settled_slice(
            world.engine, world.checkpoint, world.gate, no_second_approval, world.advance
        )
        assert next(i for i in final["incidents"] if i["id"] == case["id"])["state"] == "RESOLVED"
        truth = (await world.control.get("/_mock/ground-truth")).json()
        assert len(truth["payout_items"]) == 1 and len(world.replays) == 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_mock_script_driver_stages_dispute_approval_and_fault_then_resumes(
    db_schema, tmp_path
):
    async with slice_world(db_schema, tmp_path, driver_stages=True) as world:
        manual = ManualSteps(
            world.checkpoint.redactor, input_fn=lambda _: pytest.fail("mock is automatic")
        )
        for _ in range(2):
            await drive(
                world.engine,
                world.checkpoint,
                world.gate,
                world.api,
                world.http,
                target="mock",
                manual=manual,
                control=world.control,
            )
        await webhook_agrees(world.engine, world.checkpoint, advance=world.advance)
        truth = (await world.control.get("/_mock/ground-truth")).json()
        assert len(truth["payout_items"]) == 1
        assert len((await world.engine.snapshot())["disputes"]) == 1
        assert len(world.replays) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "signature,status",
    [
        ("UNKNOWN", "PENDING"),
        ("FAILURE", "REJECTED"),
        ("SUCCESS", "PENDING"),
        ("SUCCESS", "PROCESSED"),
    ],
)
async def test_exact_webhook_proof_checks_both_keys_signature_and_processing(
    tmp_path, signature, status
):
    store = checkpoint(tmp_path)
    store.checkpoint(batch_ids=["BATCH-THIS-RUN"], item_ids=["ITEM-THIS-RUN"])
    queries = []
    rows = [
        {
            "event_id": "wrong-resource",
            "event_type": "PAYMENT.PAYOUTS-ITEM.SUCCEEDED",
            "resource_id": "OTHER-ITEM",
            "signature": "SUCCESS",
            "status": "PROCESSED",
        },
        {
            "event_id": "wrong-type",
            "event_type": "PAYMENT.PAYOUTS-ITEM.FAILED",
            "resource_id": "ITEM-THIS-RUN",
            "signature": "SUCCESS",
            "status": "PROCESSED",
        },
        {
            "event_id": "exact",
            "event_type": "PAYMENT.PAYOUTS-ITEM.SUCCEEDED",
            "resource_id": "ITEM-THIS-RUN",
            "signature": signature,
            "status": status,
        },
    ]

    class ReadOnlyConnection:
        async def execute(self, query, params):
            assert query.startswith("SELECT ")
            assert "WHERE event_type=%s AND resource_id=%s" in query
            queries.append(params)
            matched = [r for r in rows if (r["event_type"], r["resource_id"]) == params]

            async def fetchall():
                return matched

            return SimpleNamespace(fetchall=fetchall)

    @asynccontextmanager
    async def tx():
        yield ReadOnlyConnection()

    async def nothing():
        pass

    engine = SimpleNamespace(database=SimpleNamespace(tx=tx), tick=nothing)
    if (signature, status) == ("SUCCESS", "PROCESSED"):
        await webhook_agrees(engine, store, advance=nothing, poll_limit=1)
        assert '"event_id": "exact"' in (tmp_path / "run.jsonl").read_text()
    else:
        with pytest.raises(SliceError, match="EXACT_PAYOUT_WEBHOOK_PENDING"):
            await webhook_agrees(engine, store, advance=nothing, poll_limit=1)
        assert ("PAYMENT.PAYOUTSBATCH.SUCCESS", "BATCH-THIS-RUN") in queries
    assert ("PAYMENT.PAYOUTS-ITEM.SUCCEEDED", "ITEM-THIS-RUN") in queries
    store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change,code",
    [
        (None, None),
        ("id", "WEBHOOK_ID_URL_MISMATCH"),
        ("url", "WEBHOOK_ID_URL_MISMATCH"),
        ("events", "REQUIRED_WEBHOOK_EVENTS_MISSING"),
    ],
)
async def test_registry_requires_exact_webhook_id_url_and_events(change, code):
    entry = {
        "id": "WH-THIS-RUN",
        "url": "https://slice.example.test/webhooks/paypal",
        "event_types": [{"name": "*"}],
    }
    if change == "events":
        entry["event_types"] = [{"name": "CUSTOMER.DISPUTE.CREATED"}]
    elif change:
        entry[change] = "other"

    def respond(request):
        if request.url.path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "FAKE-TOKEN", "expires_in": 300})
        assert request.method == "GET" and request.url.path == "/v1/notifications/webhooks"
        return httpx.Response(200, json={"webhooks": [entry]})

    async with PayPalHTTP(PayPalSettings(), transport=httpx.MockTransport(respond)) as http:
        if code:
            with pytest.raises(SliceError, match=code):
                await check_webhook(
                    http, "WH-THIS-RUN", "https://slice.example.test/webhooks/paypal"
                )
        else:
            await check_webhook(http, "WH-THIS-RUN", "https://slice.example.test/webhooks/paypal")


@pytest.mark.asyncio
async def test_receiver_uses_loopback_and_disables_background_workers(monkeypatch):
    import uvicorn

    from countersign import main as app_main
    from scripts import slice_e2e

    settings = Settings("postgresql://fake:fake@localhost/slice_test")
    application = FastAPI()
    seen = []
    monkeypatch.setattr(slice_e2e, "runtime_settings", lambda *_: (settings, {}))

    def create_receiver(**options):
        assert options == {"settings": settings, "start_worker": False}
        return application

    class Server:
        def __init__(self, config):
            assert config.app is application and config.host == "127.0.0.1"
            assert config.port == 8001 and config.access_log is False

        async def serve(self):
            seen.append("served")

    monkeypatch.setattr(app_main, "create_app", create_receiver)
    monkeypatch.setattr(uvicorn, "Server", Server)
    await serve_cli(SimpleNamespace(target="sandbox", env_file="unused", port=8001))
    assert seen == ["served"]


@pytest.mark.asyncio
@pytest.mark.parametrize("compressed", [False, True])
async def test_resource_log_forwards_decoded_get_and_retains_only_resource_ids(
    tmp_path, compressed
):
    store = checkpoint(tmp_path)
    body = {
        "payout_batch_id": "BATCH-1",
        "payout_item_id": "ITEM-1",
        "receiver": "fake@example.test",
        "private": "FAKE-PRIVATE",
    }
    consumed = []

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            consumed.append(True)
            content = json.dumps(body).encode()
            yield gzip.compress(content) if compressed else content

    async def respond(_):
        return httpx.Response(
            200, headers={"Content-Encoding": "gzip"} if compressed else {}, stream=Stream()
        )

    transport = ResourceLog(httpx.MockTransport(respond), store)
    response = await transport.handle_async_request(
        httpx.Request("GET", "http://localhost:8099/v1/payments/payouts-item/ITEM-1")
    )
    assert consumed
    assert response.json() == body
    assert store.data["batch_ids"] == ["BATCH-1"] and store.data["item_ids"] == ["ITEM-1"]
    public = (tmp_path / "run.jsonl").read_text()
    assert "fake@example.test" not in public and "FAKE-PRIVATE" not in public
    await response.aclose()
    store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changed,code", [("reserve", "A_RESERVE_LOST"), ("incident", "PAYOUT_INCIDENT_NOT_RESOLVED")]
)
async def test_completed_checkpoint_cannot_report_stale_financial_success(tmp_path, changed, code):
    store = checkpoint(tmp_path)
    store.checkpoint(recovery_released=True, payout_incident_id="CASE-1")
    store.state["steps"] = dict.fromkeys(
        ("plan", "owner_approval", "lost_response", "incident_pending", "reconciled"), "SUCCEEDED"
    )
    snapshot = {
        "makers": [
            {
                "display_name": "Maker A",
                "reserved": {"minor": 0 if changed == "reserve" else 180},
                "settled_last_30d": {"minor": 0},
            },
            {"display_name": "Maker B", "settled_last_30d": {"minor": 180}},
        ],
        "operations": [{"state": "SUCCEEDED"}],
        "incidents": [{"id": "CASE-1", "state": "OPEN" if changed == "incident" else "RESOLVED"}],
    }

    async def current_snapshot():
        return snapshot

    async def no_write(*_):
        pytest.fail("completed steps must not repeat a write")

    with pytest.raises(SliceError, match=code):
        await settled_slice(
            SimpleNamespace(snapshot=current_snapshot), store, None, no_write, no_write
        )
    store.close()
