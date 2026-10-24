"""Exact balances and unsigned event-history hints, with isolated PostgreSQL data."""

from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from countersign.config import PayPalSettings
from countersign.db import Database, Repositories
from countersign.paypal.http import PayPalHTTP
from countersign.paypal.read import PayPalRead
from countersign.reconcile.backfill import backfill_events
from countersign.reconcile.balances import reconcile_balances
from countersign.reconcile.common import ReconcileError, StageResult

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
NOW = datetime(2026, 11, 6, 12, tzinfo=UTC)
BALANCES = "/v1/reporting/balances"
HISTORY = "/v1/notifications/webhooks-events"


def balance(currency="USD", available="10.00", withheld="2.00", total="12.00"):
    return {
        "currency": currency,
        "available_balance": {"currency_code": currency, "value": available},
        "withheld_balance": {"currency_code": currency, "value": withheld},
        "total_balance": {"currency_code": currency, "value": total},
    }


def observation(*entries):
    return {
        "balances": list(entries or (balance(),)),
        "as_of_time": "2026-11-06T09:00:00Z",
        "last_refresh_time": "2026-11-06T08:55:00Z",
        "account_email": "buyer@example.com",
        "access_token": "fake-sensitive-token",
    }


@asynccontextmanager
async def reader_for(routes):
    calls = []

    def respond(request):
        if request.url.path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "mock-token", "expires_in": 3600})
        calls.append(str(request.url))
        data = routes.get(request.url.path)
        if isinstance(data, tuple):
            status, data = data
        else:
            status = 200
        return httpx.Response(status, json=data)

    async with PayPalHTTP(PayPalSettings(), transport=httpx.MockTransport(respond)) as http:
        yield PayPalRead(http), calls


async def test_balance_currencies_are_exact_deduplicated_and_redacted(test_db_url):
    payload = observation(balance(), balance("JPY", "100", "25", "125"))
    async with Database(test_db_url) as db, reader_for({BALANCES: payload}) as (reader, _):
        async with db.tx() as conn:
            first = await reconcile_balances(conn, reader, now=NOW)
            second = await reconcile_balances(conn, reader, now=NOW + timedelta(seconds=10))
        assert (first.seen, first.updated, second.skipped) == (2, 2, 2)
        async with db.tx() as conn:
            rows = await Repositories(conn).balance_snapshots.find()
        values = {row["currency"]: row for row in rows}
        assert len(values) == 2
        assert values["USD"]["available_minor"] == 1000
        assert values["JPY"]["total_minor"] == 125
        assert values["USD"]["last_refresh_time"].hour == 8
        assert all(row["id"].version == 7 for row in rows)
        assert "buyer@example.com" not in str(rows)
        assert "fake-sensitive-token" not in str(rows)


async def test_missing_amounts_and_freshness_remain_unknown(test_db_url):
    payload = {"balances": [{"currency": "USD", "available_balance": None}]}
    async with Database(test_db_url) as db, reader_for({BALANCES: payload}) as (reader, _):
        async with db.tx() as conn:
            await reconcile_balances(conn, reader, now=NOW)
            row = (await Repositories(conn).balance_snapshots.find())[0]
        assert all(
            row[name] is None
            for name in (
                "available_minor",
                "withheld_minor",
                "total_minor",
                "as_of_time",
                "last_refresh_time",
            )
        )


@pytest.mark.parametrize(
    "bad",
    [
        observation(balance(total="12.01")),
        observation(balance(), balance("EUR", available="1.234")),
        observation(balance(), balance()),
        {"balances": [{"currency": "XYZ"}]},
        {
            "balances": [
                {"currency": "USD", "total_balance": {"currency_code": "EUR", "value": "1"}}
            ]
        },
        {"balances": [balance()], "as_of_time": "2026-11-06T10:00:00"},
        {"balances": [balance()], "last_refresh_time": "not-a-time"},
        {"balances": "not-a-list"},
        {"balances": [None]},
    ],
)
async def test_invalid_multicurrency_observation_keeps_last_good_snapshot(test_db_url, bad):
    routes = {BALANCES: observation()}
    async with Database(test_db_url) as db, reader_for(routes) as (reader, _):
        async with db.tx() as conn:
            await reconcile_balances(conn, reader, now=NOW)
        routes[BALANCES] = bad
        with pytest.raises(ReconcileError, match="Invalid balance observation"):
            async with db.tx() as conn:
                await reconcile_balances(conn, reader, now=NOW + timedelta(seconds=1))
        async with db.tx() as conn:
            rows = await Repositories(conn).balance_snapshots.find()
        assert len(rows) == 1
        assert rows[0]["available_minor"] == 1000


async def test_delayed_balance_refresh_cannot_replace_newer_snapshot(test_db_url):
    routes = {BALANCES: observation()}
    async with Database(test_db_url) as db, reader_for(routes) as (reader, _):
        async with db.tx() as conn:
            await reconcile_balances(conn, reader, now=NOW)
        routes[BALANCES] = observation(balance(available="100.00", total="102.00"))
        routes[BALANCES]["last_refresh_time"] = "2026-11-06T08:00:00Z"
        async with db.tx() as conn:
            result = await reconcile_balances(conn, reader, now=NOW + timedelta(seconds=1))
            rows = await Repositories(conn).balance_snapshots.find()
        assert result.skipped == 1
        assert len(rows) == 1
        assert rows[0]["available_minor"] == 1000


async def test_backfill_is_disabled_without_any_network_or_database_access():
    assert await backfill_events(None, None, now=NOW) == StageResult()


def event(event_id="WH-1", event_type="CUSTOMER.DISPUTE.UPDATED", resource=None):
    return {
        "id": event_id,
        "event_type": event_type,
        "resource": resource
        if resource is not None
        else {"dispute_id": "PP-D-1", "status": "RESOLVED"},
    }


async def test_backfill_gets_fresh_resource_and_never_invents_signature_success(test_db_url):
    fresh = {"dispute_id": "PP-D-1", "status": "WAITING_FOR_SELLER_RESPONSE"}
    routes = {HISTORY: {"events": [event()]}, "/v1/customer/disputes/PP-D-1": fresh}
    observed = []

    async def on_resource(kind, identifier, detail):
        observed.append((kind, identifier, detail))
        return StageResult()

    async with Database(test_db_url) as db, reader_for(routes) as (reader, calls):
        async with db.tx() as conn:
            result = await backfill_events(
                conn, reader, now=NOW, enabled=True, on_resource=on_resource
            )
        async with db.tx() as conn:
            repeat = await backfill_events(
                conn, reader, now=NOW, enabled=True, on_resource=on_resource
            )
            assert not await Repositories(conn).webhook_inbox.find()
            assert not await Repositories(conn).disputes.find()
            assert not await Repositories(conn).obligations.find()
            saved = await Repositories(conn).policies.get("reconcile.backfill:WH-1")
        assert result.updated == 1 and repeat.skipped == 1
        assert observed == [("dispute", "PP-D-1", fresh)]
        assert saved["value"]["resource_id"] == "PP-D-1"
        assert sum("/customer/disputes/" in call for call in calls) == 1


async def test_backfill_follows_pagination_and_confirms_each_supported_kind(test_db_url):
    history = [
        event("WH-I", "PAYMENT.PAYOUTS-ITEM.SUCCEEDED", {"payout_item_id": "ITEM-1"}),
        event(
            "WH-B", "PAYMENT.PAYOUTSBATCH.SUCCESS", {"batch_header": {"payout_batch_id": "BATCH-1"}}
        ),
        event("WH-C", "PAYMENT.CAPTURE.COMPLETED", {"id": "CAP-1"}),
    ]
    routes = {
        HISTORY: {"events": history[:1], "links": [{"rel": "next", "href": "/v1/history-page-2"}]},
        "/v1/history-page-2": {"events": history[1:]},
        "/v1/payments/payouts-item/ITEM-1": {"payout_item_id": "ITEM-1"},
        "/v1/payments/payouts/BATCH-1": {"batch_header": {"payout_batch_id": "BATCH-1"}},
        "/v2/payments/captures/CAP-1": {"id": "CAP-1"},
    }
    async with Database(test_db_url) as db, reader_for(routes) as (reader, calls):
        async with db.tx() as conn:
            result = await backfill_events(conn, reader, now=NOW, enabled=True)
        assert (result.seen, result.updated) == (3, 3)
        assert len(calls) == 5


@pytest.mark.parametrize("next_link", ["https://attacker.example/steal", "/v1/loop"])
async def test_backfill_rejects_unsafe_or_looping_pages_and_rolls_back(test_db_url, next_link):
    page = {"events": [event()], "links": [{"rel": "next", "href": next_link}]}
    routes = {
        HISTORY: page,
        "/v1/loop": deepcopy(page),
        "/v1/customer/disputes/PP-D-1": {"dispute_id": "PP-D-1"},
    }
    async with Database(test_db_url) as db, reader_for(routes) as (reader, calls):
        with pytest.raises(ReconcileError, match="Backfill resource confirmation failed"):
            async with db.tx() as conn:
                await backfill_events(conn, reader, now=NOW, enabled=True)
        async with db.tx() as conn:
            assert not await Repositories(conn).policies.find()
        assert all("attacker.example" not in call for call in calls)


async def test_failed_resource_confirmation_or_callback_never_checkpoints(test_db_url):
    routes = {HISTORY: {"events": [event()]}, "/v1/customer/disputes/PP-D-1": (503, {})}

    async def failure(*args):
        raise RuntimeError("fake-private-error")

    async def reported_failure(*args):
        return StageResult(errors=1)

    async with Database(test_db_url) as db, reader_for(routes) as (reader, _):
        for callback in (None, failure, reported_failure):
            with pytest.raises(ReconcileError) as error:
                async with db.tx() as conn:
                    await backfill_events(conn, reader, now=NOW, enabled=True, on_resource=callback)
            assert "fake-private-error" not in str(error.value)
            routes["/v1/customer/disputes/PP-D-1"] = {"dispute_id": "PP-D-1"}
            async with db.tx() as conn:
                assert not await Repositories(conn).policies.find()


async def test_backfill_mismatched_resource_identity_is_not_adopted(test_db_url):
    routes = {
        HISTORY: {"events": [event()]},
        "/v1/customer/disputes/PP-D-1": {"dispute_id": "PP-D-OTHER"},
    }
    async with Database(test_db_url) as db, reader_for(routes) as (reader, _):
        with pytest.raises(ReconcileError):
            async with db.tx() as conn:
                await backfill_events(conn, reader, now=NOW, enabled=True)
        async with db.tx() as conn:
            assert not await Repositories(conn).policies.find()
