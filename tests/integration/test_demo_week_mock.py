"""C25's staged week uses real core services and the local PayPal mock only."""

import json
from collections import Counter
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from countersign.api import owner as owner_api
from countersign.approvals import ApprovalService
from countersign.config import PayPalSettings, Settings
from countersign.db import Database, Repositories
from countersign.fsm.dispute import OUTCOME_MAP, Phase
from countersign.harness import Engine, EngineSettings
from countersign.paypal.http import PayPalHTTP
from mock_paypal.app import create_app
from scripts import demo_week as demo
from scripts.sandbox_checks.manual import ManualSteps
from scripts.sandbox_checks.redact import Redactor
from scripts.slice_e2e import Checkpoint, SliceError

MONDAY = datetime(2026, 11, 2, 9, tzinfo=UTC)
BASE = "http://localhost:8099"
EMAILS = {key: "maker-" + key.lower() + "@example.test" for key in ("A", "B", "C")}
EMAILS["C_OLD"] = "never-registered-c@example.test"
ORDERS = {
    key: {"order_id": "ORDER-" + key, "capture_id": "CAPTURE-" + key}
    for key in ("A400", "B540", "B60", "C333")
}
PRIOR = "PRIOR-C-ITEM"


@asynccontextmanager
async def demo_world(db_schema, tmp_path, monkeypatch):
    holder, delivered, balances = {}, [], []

    async def deliver(url, headers, body):
        status = await holder["engine"].deliver_webhook(headers, body)
        delivered.append((body, status))
        return status

    mock = create_app(control_enabled=True, webhook_delivery=deliver)
    # Seller adjudication uses the production D10 mapping. Only buyer-close is
    # a scoped mock fixture while its real sandbox outcome remains unverified.
    behavior = mock.state.resource_service.behavior
    assert behavior.seller_win_code == "RESOLVED_SELLER_FAVOUR"
    assert OUTCOME_MAP[behavior.seller_win_code] is Phase.WON
    monkeypatch.setitem(OUTCOME_MAP, behavior.buyer_close_code, Phase.CANCELLED)
    inner = httpx.ASGITransport(app=mock)

    class ObserveBalances(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            try:
                return await inner.handle_async_request(request)
            finally:
                balances.append(mock.state.store.payouts.available_minor)

    observed = ObserveBalances()
    store = Checkpoint(
        tmp_path / "week",
        target="mock",
        identity={"test": "C25"},
        redactor=Redactor(
            secrets=("FAKE-SECRET", "FAKE-TOKEN"),
            email_aliases={
                email: "maker-" + key.lower() + "@redacted" for key, email in EMAILS.items()
            },
        ),
        clock=lambda: mock.state.store.clock.now,
        output=lambda _: None,
    )
    store.checkpoint(monday=MONDAY.isoformat(), orders=ORDERS, prior_item_id=PRIOR)
    trace = demo.PayoutTrace(observed, store)
    try:
        async with (
            Database(db_schema.conninfo) as database,
            httpx.AsyncClient(transport=observed, base_url=BASE) as control,
        ):
            assert (
                await control.post("/_mock/clock/set", json={"now": MONDAY.isoformat()})
            ).status_code == 200
            assert (
                await control.post(
                    "/_mock/seed", json=demo.mock_seed(EMAILS, ORDERS, PRIOR, MONDAY)
                )
            ).status_code == 200
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
                json={
                    "url": "http://localhost:8000/webhooks/paypal",
                    "event_types": [{"name": "*"}],
                },
            )
            assert registered.status_code == 201
            model = demo.DemoModel()
            config = EngineSettings(
                db_schema.conninfo,
                BASE,
                "fake-client",
                "FAKE-SECRET",
                registered.json()["id"],
                lambda: mock.state.store.clock.now,
                llm=model,
            )
            async with (
                PayPalHTTP(
                    PayPalSettings(BASE, "fake-client", "FAKE-SECRET", config.webhook_id),
                    transport=trace,
                    monotonic=lambda: mock.state.store.clock.now.timestamp(),
                ) as http,
                Engine(
                    config,
                    database=database,
                    http=http,
                    agent_tools=demo.read_registry(http, database),
                ) as engine,
            ):
                holder["engine"] = engine
                app = FastAPI()
                with monkeypatch.context() as clocks:
                    clocks.setattr(
                        owner_api,
                        "ApprovalService",
                        lambda db, **kw: ApprovalService(db, clock=config.clock, **kw),
                    )
                    app.include_router(
                        owner_api.build_router(
                            database, Settings("postgresql://fake:fake@localhost/demo_test")
                        ),
                        prefix="/api/v1",
                    )
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://localhost:8000"
                ) as owner:

                    async def advance_to(at):
                        seconds = max(0, int((at - config.clock()).total_seconds()))
                        result = await control.post(
                            "/_mock/clock/advance", json={"seconds": seconds}
                        )
                        assert result.status_code == 200

                    manual = ManualSteps(
                        store.redactor,
                        input_fn=lambda _: pytest.fail("mock week must need no human input"),
                        output_fn=lambda _: None,
                    )
                    yield SimpleNamespace(
                        engine=engine,
                        store=store,
                        http=http,
                        control=control,
                        owner=owner,
                        model=model,
                        advance_to=advance_to,
                        manual=manual,
                        database=database,
                        mock=mock,
                        balances=balances,
                        delivered=delivered,
                    )
    finally:
        store.close()


def maker(snapshot, name):
    return next(row for row in snapshot["makers"] if row["display_name"] == "Maker " + name)


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize("settlement", ["adjudicate", "buyer_close"])
@pytest.mark.parametrize("evidence_available", [False, True])
async def test_whole_week_pays_exactly_once_and_keeps_each_safety_moment(
    db_schema, tmp_path, monkeypatch, settlement, evidence_available
):
    async with demo_world(db_schema, tmp_path, monkeypatch) as world:
        options = demo.DemoOptions(settlement=settlement, evidence_available=evidence_available)
        report = await demo.run_week(
            world.engine,
            world.store,
            http=world.http,
            control=world.control,
            owner=world.owner,
            model=world.model,
            advance_to=world.advance_to,
            manual=world.manual,
            target="mock",
            options=options,
            emails=EMAILS,
        )
        planned, friday, final = report["friday_planned"], report["friday_settled"], report["final"]
        assert maker(planned, "A")["reserved"]["minor"] == 36000
        assert maker(planned, "B")["reserved"]["minor"] == 5400
        [run] = [r for r in planned["runs"] if r["week_start"] == "2026-11-02"]
        assert run["total"]["minor"] == 108600 and run["replacement_count"] == 1
        assert planned["cash"]["unreserved"]["minor"] == 25400
        assert maker(friday, "A")["settled_last_30d"]["minor"] == 0
        assert maker(friday, "A")["reserved"]["minor"] == 36000
        assert maker(friday, "B")["settled_last_30d"]["minor"] == 48600
        assert maker(friday, "C")["settled_last_30d"]["minor"] == 60000
        assert maker(final, "A")["settled_last_30d"]["minor"] == 36000
        assert maker(final, "A")["reserved"]["minor"] == 0
        assert maker(final, "B")["reserved"]["minor"] == 5400
        released = report["following_release"]
        assert maker(released, "A")["reserved"]["minor"] == 0
        assert maker(released, "A")["settled_last_30d"]["minor"] == 0
        assert any(
            d["phase"] == ("WON" if settlement == "adjudicate" else "CANCELLED")
            for d in released["disputes"]
        )
        assert min(world.balances) >= 0
        protected, naive = report["simulation"]["protected"], report["simulation"]["naive"]
        assert (
            protected["payout"]["minor"] == 108600
            and protected["unreserved_after"]["minor"] == 25400
        )
        assert (
            naive["payout"]["minor"] == 150000 and naive["effective_available"]["minor"] == 134000
        )
        assert naive["pays_disputed_shares"] is True and naive["shortfall"]["minor"] == 16000
        truth = (await world.control.get("/_mock/ground-truth")).json()
        assert len(truth["payout_items"]) == 5 and truth["refunds"] == []
        assert (
            next(item for item in truth["payout_items"] if item["payout_item_id"] == PRIOR)[
                "status"
            ]
            == "RETURNED"
        )
        paid = [item for item in truth["payout_items"] if item["status"] == "SUCCESS"]
        assert sorted((p["receiver"], p["amount"]["value"]) for p in paid) == sorted(
            [
                (EMAILS["A"], "360.00"),
                (EMAILS["B"], "486.00"),
                (EMAILS["C"], "300.00"),
                (EMAILS["C"], "300.00"),
            ]
        )
        async with world.database.tx() as conn:
            repos = Repositories(conn)
            proposals = await repos.proposals.find(limit=1000)
            by_action = {}
            for proposal in proposals:
                by_action.setdefault(proposal["action"], []).append(proposal)
            assert any(
                p["state"] == "EXECUTED" and p["verdict"] == "ALLOW"
                for p in by_action["CANCEL_UNCLAIMED_PAYOUT"]
            )
            assert any(p["state"] == "NEEDS_OWNER" for p in by_action["ACCEPT_CLAIM"])
            injected = [p for p in by_action["ACCEPT_CLAIM"] if p["state"] == "BLOCKED"]
            assert injected
            rules = {
                r["rule"]: r["outcome"] for r in injected[-1]["verdict_details"]["rule_results"]
            }
            assert rules["INJECTION_GUARD"] == rules["PAYEE_INTEGRITY"] == "BLOCK"
            assert any(
                flag["type"] == "PROMPT_INJECTION" for flag in injected[-1]["raw_output"]["flags"]
            )
            retries = [p for p in by_action["CREATE_REPLACEMENT_PAYOUT"] if p["state"] == "BLOCKED"]
            assert retries and any(
                r["rule"] == "SETTLEMENT" and r["outcome"] == "BLOCK"
                for r in retries[-1]["verdict_details"]["rule_results"]
            )
            evidence = by_action["PROVIDE_EVIDENCE"]
            assert evidence[-1]["state"] == ("EXECUTED" if evidence_available else "BLOCKED")
            assert any(
                p["action"] == "DRAFT_MESSAGE" and p["state"] == "EXECUTED" for p in proposals
            )
            [cancel] = [
                op
                for op in await repos.operations.find(limit=1000)
                if op["kind"] == "PAYOUT_CANCEL"
            ]
            [resend] = [
                a for a in await repos.payout_attempts.find(limit=1000) if a["attempt_no"] == 2
            ]
            assert resend["prepared_at"] >= cancel["completed_at"]
            journals = await repos.ledger_journals.find(limit=1000)
            assert sum(j["memo"].startswith("PAYOUT_SUCCESS:") for j in journals) == 4
        logs = (world.store.directory / "run.jsonl").read_text(encoding="utf-8")
        assert all(email not in logs for email in EMAILS.values())
        assert "FAKE-SECRET" not in logs and "FAKE-TOKEN" not in logs
        events = [json.loads(line) for line in logs.splitlines()]
        names = {event["event"] for event in events}
        assert {
            "SAFETY_MOMENT_1",
            "SAFETY_1_DUPLICATE_LINK",
            "SAFETY_1_RECOVERED",
            "SAFETY_MOMENT_2_BLOCKED",
            "SAFETY_MOMENT_3_BLOCKED",
        } <= names
        posts = [event for event in events if event["event"] == "PAYOUT_POST"]
        counts = Counter(event["request_id"] for event in posts)
        assert sorted(counts.values()) == [1, 1, 1, 2]
        [replayed] = [key for key, count in counts.items() if count == 2]
        assert len({p["body_hash"] for p in posts if p["request_id"] == replayed}) == 1
        duplicate = next(e for e in events if e["event"] == "SAFETY_1_DUPLICATE_LINK")
        assert duplicate["request_id"] == replayed and duplicate["response_status"] == 400
        assert duplicate["original_batch_link"].startswith(BASE + "/v1/payments/payouts/")
        batch_path = duplicate["original_batch_link"][len(BASE) :]
        assert any(r["method"] == "GET" and r["path"] == batch_path for r in truth["requests"])
        # Restart never repeats owner approvals, cancel/evidence, settlement or payouts.
        before = [(r["method"], r["path"]) for r in truth["requests"] if r["method"] == "POST"]
        await demo.run_week(
            world.engine,
            world.store,
            http=world.http,
            control=world.control,
            owner=world.owner,
            model=world.model,
            advance_to=world.advance_to,
            manual=world.manual,
            target="mock",
            options=options,
            emails=EMAILS,
        )
        resumed = (await world.control.get("/_mock/ground-truth")).json()
        assert [
            (r["method"], r["path"]) for r in resumed["requests"] if r["method"] == "POST"
        ] == before


@pytest.mark.parametrize(
    "database",
    [
        "postgresql://fake:fake@localhost/work",
        "postgresql://localhost/work?application_name=safe_demo",
        "dbname=work application_name=safe_test",
        "sqlite:///safe_demo",
    ],
)
def test_cli_guard_refuses_a_working_or_deceptively_named_database(database):
    with pytest.raises(Exception, match="DATABASE|DEMO"):
        demo.database_name_guard(database)


@pytest.mark.parametrize("database", ["postgresql://localhost/week_demo", "dbname=week_test"])
def test_cli_guard_accepts_only_explicit_disposable_database_names(database):
    demo.database_name_guard(database)


def test_runtime_guard_rejects_the_working_database_without_exposing_its_password(monkeypatch):
    settings = Settings("postgresql://fake:FAKE-SECRET@localhost/working")
    monkeypatch.setattr(demo, "load_settings", lambda: settings)
    with pytest.raises(SliceError, match="DEMO_DATABASE_REFUSED") as caught:
        demo.runtime_settings("mock", "unused")
    assert "FAKE-SECRET" not in str(caught.value)


def test_cli_defaults_to_d10_confirmed_adjudication(monkeypatch):
    observed = []

    async def fake_runtime(args):
        observed.append(args.settlement)

    monkeypatch.setattr(demo, "run_cli", fake_runtime)
    assert demo.main(["--target", "mock"]) == 0
    assert observed == ["adjudicate"]
    assert demo.DemoOptions().settlement == "adjudicate"


@pytest.mark.parametrize("settlement", ["adjudicate", "buyer_close"])
def test_sandbox_missing_outcome_mapping_stops_before_connections(monkeypatch, capsys, settlement):
    settings = Settings(
        "postgresql://fake:FAKE-SECRET@localhost/week_demo",
        mode="live",
        owner_token="FAKE-OWNER-TOKEN",
        paypal=PayPalSettings("https://api-m.sandbox.paypal.com", "fake", "FAKE-SECRET", "WH-FAKE"),
    )
    monkeypatch.setattr(demo, "runtime_settings", lambda *_: (settings, EMAILS))
    monkeypatch.setattr(demo, "OUTCOME_MAP", {})

    def no_connections(*_, **__):
        pytest.fail("D10 mapping refusal must precede database and provider connections")

    monkeypatch.setattr("countersign.db.Database", no_connections)
    assert (
        demo.main(
            [
                "--target",
                "sandbox",
                "--settlement",
                settlement,
                "--reason-a",
                "D10_REASON",
            ]
        )
        == 1
    )
    output = capsys.readouterr().out
    expected = (
        "D10_OUTCOME_MAPPING_REQUIRED"
        if settlement == "adjudicate"
        else "D10_BUYER_CLOSE_OUTCOME_MAPPING_REQUIRED"
    )
    assert expected in output and "FAKE-SECRET" not in output


def test_buyer_close_cannot_use_the_seller_win_mapping():
    with pytest.raises(SliceError, match="D10_BUYER_CLOSE_OUTCOME_MAPPING_REQUIRED"):
        demo.settlement_mapping_guard(demo.DemoOptions(settlement="buyer_close"))


def test_mock_buyer_close_mapping_is_scoped_and_never_available_to_sandbox():
    recorder = SimpleNamespace(record=lambda *_, **__: None)
    original = dict(OUTCOME_MAP)
    with (
        pytest.raises(RuntimeError, match="interrupted"),
        demo.mock_outcome_mapping(PayPalSettings(), recorder),
    ):
        assert OUTCOME_MAP["CANCELED_BY_BUYER"] is Phase.CANCELLED
        raise RuntimeError("interrupted")
    assert OUTCOME_MAP == original
    with (
        pytest.raises(SliceError, match="MOCK_OUTCOME_MAPPING_REFUSED"),
        demo.mock_outcome_mapping(PayPalSettings("https://api-m.sandbox.paypal.com"), recorder),
    ):
        pytest.fail("sandbox must never install the synthetic mapping")
    assert OUTCOME_MAP == original
