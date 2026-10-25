"""Non-idempotent executor writes with real transactions and fake PayPal HTTP only."""

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import timedelta

import httpx
import pytest
import test_executor as executor_tests
from test_executor import BASE, NOW, WEEK, rows

from countersign.approvals import ApprovalService
from countersign.approvals.requests import ACCEPT_NOTE
from countersign.approvals.service import load_context
from countersign.config import PayPalBehavior, PayPalSettings
from countersign.db import Repositories
from countersign.executor import Executor
from countersign.ledger import journal
from countersign.money import Money
from countersign.paypal.http import PayPalHTTP, canonical_payload_hash
from countersign.shield.engine import evaluate

world = executor_tests.world
pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def target(world, *, cancel=False):
    database, *_ = world
    async with database.tx() as conn:
        repos = Repositories(conn)
        maker = (await repos.makers.find())[0]
        if cancel:
            run = await repos.payout_runs.insert(week_start=WEEK, state="DONE")
            line = await repos.payout_lines.insert(
                run_id=run["id"],
                maker_id=maker["id"],
                amount_minor=900,
                currency="USD",
                state="PAYING",
            )
            obligation = (await repos.obligations.find({"maker_id": maker["id"]}))[0]
            await repos.obligations.update(obligation["id"], state="SCHEDULED")
            await repos.obligations.update(obligation["id"], state="IN_FLIGHT")
            await repos.payout_line_obligations.insert(
                line_id=line["id"], obligation_id=obligation["id"], amount_minor=900
            )
            original = await repos.operations.insert(
                kind="PAYOUT_CREATE",
                target_type="payout_line",
                target_id=line["id"],
                payload={},
                payload_hash=bytes(32),
                state="SUCCEEDED",
            )
            attempt = await repos.payout_attempts.insert(
                line_id=line["id"],
                attempt_no=1,
                operation_id=original["id"],
                sender_batch_id="BATCH-KEY",
                request_id="REQUEST-KEY",
                sender_item_id="ITEM-KEY",
                payee_email=maker["payout_email"],
                amount_minor=900,
                currency="USD",
                state="UNCLAIMED",
                prepared_at=NOW - timedelta(days=1),
                last_checked_at=NOW,
                paypal_batch_id="BATCH",
                paypal_item_id="ITEM",
            )
            for draft in (
                journal.payout_submitted(Money(900, "USD")),
                journal.payout_unclaimed(Money(900, "USD")),
            ):
                await journal.post_journal(
                    conn, draft, operation_id=original["id"], occurred_at=NOW
                )
            return attempt
        for key, value in {
            "deadline_close_seconds": 172800,
            "refund_limit_per_order": {"minor": 10000, "currency": "USD"},
            "refund_limit_per_day": {"minor": 20000, "currency": "USD"},
        }.items():
            await repos.policies.insert(key=key, value=value)
        order = await repos.orders.insert(
            maker_id=maker["id"],
            buyer_ref="BUYER",
            amount_minor=1000,
            currency="USD",
            paypal_capture_id="CAPTURE",
            tracking={"carrier": "UPS", "number": "TRACK-123"},
        )
        return await repos.disputes.insert(
            paypal_dispute_id="DISPUTE",
            order_id=order["id"],
            status="WAITING_FOR_SELLER_RESPONSE",
            lifecycle_stage="INQUIRY",
            amount_minor=1000,
            currency="USD",
            phase="ACTIVE",
            seller_response_due_date=NOW + timedelta(days=3),
            links=[
                {"rel": "provide_evidence", "href": "/provide-evidence", "method": "POST"},
                {"rel": "accept_claim", "href": "/accept-claim", "method": "POST"},
            ],
            raw={"disputed_transactions": [{"seller_transaction_id": "CAPTURE"}]},
        )


async def proposed(world, action, record, *, behavior=None, approve=True, flags=()):
    database, *_ = world
    behavior = behavior or PayPalBehavior()
    params = (
        {"attempt_id": str(record["id"])}
        if action == "CANCEL_UNCLAIMED_PAYOUT"
        else {"paypal_dispute_id": record["paypal_dispute_id"]}
    )
    async with database.tx() as conn:
        repos = Repositories(conn)
        row = await repos.proposals.insert(
            action=action,
            params=params,
            evidence_refs=["tracking:TRACK-123"] if action == "PROVIDE_EVIDENCE" else [],
            model="fake",
            raw_output={"flags": list(flags)},
            state="CREATED",
        )
        proposal, snap, payload = await load_context(conn, row, now=NOW, behavior=behavior)
        assert evaluate(proposal, snap).verdict != "BLOCK"
        binding = {
            "payload_hash": proposal.payload_hash,
            "record_versions": snap.record_versions(),
            "expires_at": (NOW + timedelta(minutes=30)).isoformat(),
        }
        await repos.proposals.update(
            row["id"],
            state="NEEDS_OWNER" if action == "ACCEPT_CLAIM" and approve else "ALLOWED",
            verdict_details={"operation_payload": payload, "approval_request": binding},
            raw_output={"flags": list(flags), "approval_request": binding},
        )
    if action == "ACCEPT_CLAIM" and approve:
        await ApprovalService(database, clock=world[-1], behavior=behavior).approve(
            row["id"], proposal.payload_hash
        )
    return row["id"], payload


@asynccontextmanager
async def endpoint(world, *, behavior=None, fail=False, decide=True, already=False):
    database, *_ = world
    control = {"posts": [], "gets": 0, "decide": decide, "fail": fail}

    async def respond(request):
        if request.url.path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "FAKE", "expires_in": 300})
        if request.method == "POST":
            control["posts"].append(request)
            # A different connection sees the committed write-ahead row before HTTP.
            operations = [
                r for r in await rows(database, "operations") if r["state"] == "IN_FLIGHT"
            ]
            assert len(operations) == 1
            control["operation"] = operations[0]
            approvals = await rows(database, "approvals")
            assert not approvals or approvals[0]["used_at"] == NOW
            if control["fail"]:
                raise httpx.ReadTimeout("lost response", request=request)
            if already:
                return httpx.Response(400, json={"name": "ITEM_ALREADY_CANCELLED"})
            return httpx.Response(200, json={"transaction_status": "RETURNED"})
        control["gets"] += 1
        if "payouts-item" in request.url.path:
            attempt = (await rows(database, "payout_attempts"))[0]
            return httpx.Response(
                200,
                json={
                    "payout_item_id": "ITEM",
                    "payout_batch_id": "BATCH",
                    "sender_batch_id": "BATCH-KEY",
                    "transaction_status": "RETURNED" if control["decide"] else "UNCLAIMED",
                    "payout_item": {
                        "sender_item_id": "ITEM-KEY",
                        "receiver": attempt["payee_email"],
                        "amount": {"value": "9.00", "currency": "USD"},
                    },
                },
            )
        body = {
            "dispute_id": "DISPUTE",
            "status": "WAITING_FOR_SELLER_RESPONSE",
            "links": [{"rel": "provide-evidence"}, {"rel": "accept-claim"}],
        }
        if control["decide"]:
            body.update(
                status="RESOLVED",
                links=[],
                dispute_outcome={"amount_refunded": {"value": "10.00", "currency_code": "USD"}},
            )
        return httpx.Response(200, json=body)

    async with PayPalHTTP(
        PayPalSettings(base_url=BASE, behavior=behavior or PayPalBehavior()),
        transport=httpx.MockTransport(respond),
    ) as http:
        yield Executor.from_http(database, http, clock=world[-1]), control


@pytest.mark.parametrize("action", ["CANCEL_UNCLAIMED_PAYOUT", "PROVIDE_EVIDENCE", "ACCEPT_CLAIM"])
async def test_new_actions_write_once_with_committed_binding(world, action):
    behavior = PayPalBehavior(refund_source=journal.AVAILABLE)
    record = await target(world, cancel=action == "CANCEL_UNCLAIMED_PAYOUT")
    proposal_id, payload = await proposed(world, action, record, behavior=behavior)
    async with endpoint(world, behavior=behavior) as (executor, control):
        result = await executor.execute(proposal_id)
        assert result["proposal_state"] == "EXECUTED"
        assert set(result["operations"].values()) == {"SUCCEEDED"}
        await executor.execute(proposal_id)
        await executor.sweep()
        assert len(control["posts"]) == 1
        assert canonical_payload_hash(control["operation"]["payload"]) == canonical_payload_hash(
            payload["request"]
        )
        sent = control["posts"][0]
        if action == "CANCEL_UNCLAIMED_PAYOUT":
            assert sent.content == b"" and "content-type" not in sent.headers
            assert (await rows(world[0], "payout_lines"))[0]["state"] == "UNPAID"
            assert "SCHEDULED" in {r["state"] for r in await rows(world[0], "obligations")}
        elif action == "PROVIDE_EVIDENCE":
            assert sent.headers["content-type"].startswith("multipart/form-data")
            assert b"TRACK-123" in sent.content and b"PROOF_OF_FULFILLMENT" in sent.content
        else:
            assert json.loads(sent.content) == {"note": ACCEPT_NOTE, "accept_claim_type": "REFUND"}
            assert len(await rows(world[0], "ledger_journals")) == 1


@pytest.mark.parametrize("action", ["CANCEL_UNCLAIMED_PAYOUT", "PROVIDE_EVIDENCE", "ACCEPT_CLAIM"])
async def test_ambiguous_action_is_never_resent_and_get_resolves_it(world, action):
    behavior = PayPalBehavior(refund_source=journal.WITHHELD)
    record = await target(world, cancel=action == "CANCEL_UNCLAIMED_PAYOUT")
    proposal_id, _ = await proposed(world, action, record, behavior=behavior)
    async with endpoint(world, behavior=behavior, fail=True, decide=False) as (executor, control):
        first = await executor.execute(proposal_id)
        assert set(first["operations"].values()) == {"UNKNOWN"}
        await executor.sweep()
        assert len(await rows(world[0], "incidents")) == 1
        assert (await rows(world[0], "incidents"))[0]["state"] == "AWAITING_OWNER"
        control["decide"] = True
        swept = await executor.sweep()
        assert set(swept["resolved"].values()) == {"SUCCEEDED"}
        assert len(control["posts"]) == 1
        await executor.sweep()
        assert len(control["posts"]) == 1
        if action == "ACCEPT_CLAIM":
            postings = await rows(world[0], "ledger_postings")
            assert {p["account"] for p in postings} == {journal.WITHHELD, journal.BUYER_REFUNDS}


async def test_cancel_post_returned_is_insufficient_until_get_returned(world):
    record = await target(world, cancel=True)
    proposal_id, _ = await proposed(world, "CANCEL_UNCLAIMED_PAYOUT", record)
    async with endpoint(world, decide=False) as (executor, control):
        await executor.execute(proposal_id)
        assert (await rows(world[0], "payout_attempts"))[0]["state"] == "UNCLAIMED"
        assert (await rows(world[0], "payout_lines"))[0]["state"] == "PAYING"
        control["decide"] = True
        await executor.sweep()
        assert (await rows(world[0], "payout_attempts"))[0]["state"] == "RETURNED"
        assert len(control["posts"]) == 1


async def test_already_cancelled_is_resolved_by_get(world):
    record = await target(world, cancel=True)
    proposal_id, _ = await proposed(world, "CANCEL_UNCLAIMED_PAYOUT", record)
    async with endpoint(world, already=True) as (executor, control):
        assert (await executor.execute(proposal_id))["proposal_state"] == "EXECUTED"
        assert control["gets"] == 1


async def test_refund_source_defers_once_preserving_approval_then_same_proposal_runs(world):
    record = await target(world)
    proposal_id, _ = await proposed(world, "ACCEPT_CLAIM", record)
    before = (await rows(world[0], "approvals"))[0]
    async with endpoint(world) as (executor, control):
        await asyncio.gather(executor.execute(proposal_id), executor.execute(proposal_id))
        assert (await rows(world[0], "proposals"))[0]["state"] == "APPROVED"
        assert (await rows(world[0], "approvals"))[0] == before
        assert await rows(world[0], "operations") == [] and control["posts"] == []
        audits = [
            r
            for r in await rows(world[0], "audit_log")
            if (r["details"] or {}).get("reason") == "REFUND_SOURCE_UNCONFIGURED"
        ]
        assert len(audits) == 1
    async with endpoint(world, behavior=PayPalBehavior(refund_source=journal.AVAILABLE)) as (
        executor,
        control,
    ):
        assert (await executor.execute(proposal_id))["proposal_state"] == "EXECUTED"
        assert len(control["posts"]) == 1


@pytest.mark.parametrize("change", ["tracking", "target", "format", "expiry", "flag"])
async def test_changed_request_or_approval_blocks_before_http(world, change):
    record = await target(world)
    action = "ACCEPT_CLAIM" if change == "expiry" else "PROVIDE_EVIDENCE"
    behavior = PayPalBehavior(refund_source=journal.AVAILABLE)
    proposal_id, _ = await proposed(world, action, record, behavior=behavior)
    async with world[0].tx() as conn:
        repos = Repositories(conn)
        if change == "tracking":
            await repos.orders.update(
                record["order_id"], tracking={"carrier": "UPS", "number": "CHANGED"}
            )
        elif change == "target":
            await repos.disputes.update(record["id"], paypal_dispute_id="DIFFERENT")
        elif change == "expiry":
            world[-1].now += timedelta(minutes=31)
        elif change == "flag":
            await repos.proposals.update(
                proposal_id, raw_output={"flags": [{"type": "PROMPT_INJECTION"}]}
            )
    if change == "format":
        behavior = PayPalBehavior(evidence_format="json")
    async with endpoint(world, behavior=behavior) as (executor, control):
        assert (await executor.execute(proposal_id))["proposal_state"] == "BLOCKED_AT_EXECUTION"
        assert not control["posts"]
        assert all(a["used_at"] is None for a in await rows(world[0], "approvals"))


@pytest.mark.parametrize("change", ["status", "link", "tracking"])
async def test_evidence_preconditions_are_rechecked_at_execution(world, change):
    record = await target(world)
    proposal_id, _ = await proposed(world, "PROVIDE_EVIDENCE", record)
    async with world[0].tx() as conn:
        repos = Repositories(conn)
        if change == "status":
            await repos.disputes.update(record["id"], status="WAITING_FOR_BUYER_RESPONSE")
        elif change == "link":
            await repos.disputes.update(record["id"], links=[])
        else:
            await repos.orders.update(record["order_id"], tracking=None)
    async with endpoint(world) as (executor, control):
        assert (await executor.execute(proposal_id))["proposal_state"] == "BLOCKED_AT_EXECUTION"
        assert not control["posts"]


async def test_evidence_uses_current_status_and_link_not_lifecycle_stage(world):
    record = await target(world)
    proposal_id, _ = await proposed(world, "PROVIDE_EVIDENCE", record)
    async with world[0].tx() as conn:
        await Repositories(conn).disputes.update(
            record["id"], lifecycle_stage="ARBITRARY_NEW_STAGE"
        )
    async with endpoint(world) as (executor, control):
        assert (await executor.execute(proposal_id))["proposal_state"] == "EXECUTED"
        assert len(control["posts"]) == 1


async def test_unapproved_accept_claim_is_blocked(world):
    behavior = PayPalBehavior(refund_source=journal.AVAILABLE)
    record = await target(world)
    proposal_id, _ = await proposed(world, "ACCEPT_CLAIM", record, behavior=behavior, approve=False)
    # The unapproved fixture starts with a CREATED -> ALLOWED verdict; it never
    # rewrites an existing NEEDS_OWNER decision into an autonomous approval.
    async with endpoint(world, behavior=behavior) as (executor, control):
        assert (await executor.execute(proposal_id))["proposal_state"] == "BLOCKED_AT_EXECUTION"
        assert not control["posts"] and not await rows(world[0], "ledger_journals")


@pytest.mark.parametrize("action", ["CANCEL_UNCLAIMED_PAYOUT", "PROVIDE_EVIDENCE", "ACCEPT_CLAIM"])
async def test_crashed_non_idempotent_write_is_get_only_after_restart(world, action):
    behavior = PayPalBehavior(refund_source=journal.WITHHELD)
    record = await target(world, cancel=action == "CANCEL_UNCLAIMED_PAYOUT")
    proposal_id, _ = await proposed(world, action, record, behavior=behavior)
    async with endpoint(world, behavior=behavior) as (executor, control):
        operations = await executor._prepare(proposal_id)
        assert len(operations) == 1
        # Crash could have happened before POST or after its response was lost.
        world[-1].now += timedelta(minutes=11)
        swept = await executor.sweep()
        assert swept["recovered"] == 1
        assert set(swept["resolved"].values()) == {"SUCCEEDED"}
        assert control["posts"] == [] and control["gets"] == 1


async def test_two_executors_take_one_nonpaying_proposal_once(world):
    record = await target(world)
    proposal_id, _ = await proposed(world, "PROVIDE_EVIDENCE", record)
    async with endpoint(world) as (executor, control):
        await asyncio.gather(executor.execute(proposal_id), executor.execute(proposal_id))
        assert len(control["posts"]) == 1


async def test_unknown_available_refund_reserves_cash_until_confirmation(world):
    record = await target(world)
    proposal_id, _ = await proposed(world, "ACCEPT_CLAIM", record)
    async with endpoint(
        world, behavior=PayPalBehavior(refund_source=journal.AVAILABLE), fail=True, decide=False
    ) as (executor, control):
        await executor.execute(proposal_id)
        [reservation] = await rows(world[0], "reservations")
        assert reservation["state"] == "ACTIVE" and reservation["amount_minor"] == 1000
        assert await rows(world[0], "ledger_journals") == []
        control["decide"] = True
        await executor.sweep()
        assert (await rows(world[0], "reservations"))[0]["state"] == "CONSUMED"
        assert len(await rows(world[0], "ledger_journals")) == 1


async def test_funding_source_is_frozen_for_unknown_refund(world):
    record = await target(world)
    proposal_id, _ = await proposed(world, "ACCEPT_CLAIM", record)
    async with endpoint(
        world, behavior=PayPalBehavior(refund_source=journal.WITHHELD), fail=True, decide=False
    ) as (executor, _):
        await executor.execute(proposal_id)
    async with endpoint(world, behavior=PayPalBehavior(refund_source=journal.AVAILABLE)) as (
        executor,
        control,
    ):
        await executor.sweep()
        assert not control["posts"]
    assert {p["account"] for p in await rows(world[0], "ledger_postings")} == {
        journal.WITHHELD,
        journal.BUYER_REFUNDS,
    }
