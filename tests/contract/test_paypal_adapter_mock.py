"""Adapter contract checks against the teammate's local in-process PayPal mock."""

import asyncio
import copy

import httpx
import pytest

from countersign.config import PayPalSettings
from countersign.money import Money
from countersign.paypal.http import Ambiguous, DefiniteError, Ok, PayPalHTTP, canonical_payload_hash
from countersign.paypal.read import PayPalRead
from countersign.paypal.sandbox_controls import SandboxControls
from countersign.paypal.webhook_verify import Verification, WebhookVerifier
from countersign.paypal.write import PayPalWrite
from mock_paypal.app import create_app

BASE = "http://localhost:8099"
ID = "019a12de-7598-7000-8000-000000000002"
KEY = f"cs-{ID}-a1"


def persisted(kind="PAYOUT_CREATE", *, body=None, target=None, key=KEY):
    if body is None:
        body = {
            "sender_batch_header": {"sender_batch_id": key},
            "items": [
                {
                    "recipient_type": "EMAIL",
                    "receiver": "fake-maker@example.test",
                    "amount": {"value": "1.00", "currency": "USD"},
                    "sender_item_id": key + "-i1",
                }
            ],
        }
    payload = {"body": body, "request_id": key, **(target or {})}
    return {
        "id": ID,
        "kind": kind,
        "state": "IN_FLIGHT",
        "payload": payload,
        "payload_hash": canonical_payload_hash(payload),
    }


@pytest.mark.asyncio
async def test_mock_payout_read_cancel_replay_and_balances():
    app = create_app(control_enabled=True)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as controls:
        assert (
            await controls.post("/_mock/seed", json={"balances": {"available": "1500.00"}})
        ).status_code == 200
        async with PayPalHTTP(PayPalSettings(base_url=BASE), transport=transport) as http:
            writer, reader = PayPalWrite(http), PayPalRead(http)
            row = persisted()
            result = await writer.create_payout_single_item(row)
            assert isinstance(result, Ok) and result.resp.status_code == 201
            batch_id = result.resp.body["batch_header"]["payout_batch_id"]
            assert "items" not in result.resp.body
            replay = copy.deepcopy(row)
            replay["state"] = "UNKNOWN"
            repeated = await writer.create_payout_single_item(replay)
            assert isinstance(repeated, Ok)
            assert repeated.resp.body["batch_header"]["payout_batch_id"] == batch_id
            batch = await reader.get_payout_batch(batch_id)
            assert batch.amounts["/batch_header/amount"] == Money(100, "USD")
            item_id = batch.body["items"][0]["payout_item_id"]
            assert (await reader.follow(batch.body["links"][0]["href"])).status_code == 200
            await controls.post("/_mock/clock/advance", json={"seconds": 30})
            item = await reader.get_payout_item(item_id)
            assert item.body["transaction_status"] == "UNCLAIMED"
            assert item.amounts["/payout_item/amount"] == Money(100, "USD")
            cancel = persisted("PAYOUT_CANCEL", body={}, target={"payout_item_id": item_id})
            canceled = await writer.cancel_item(cancel)
            assert isinstance(canceled, Ok)
            assert canceled.resp.body["transaction_status"] == "RETURNED"
            duplicate_cancel = await writer.cancel_item(cancel)
            assert isinstance(duplicate_cancel, DefiniteError)
            cancel["state"] = "UNKNOWN"
            assert isinstance(await writer.cancel_item(cancel), Ambiguous)
            balances = await reader.get_balances()
            assert balances.amounts["/balances/0/available_balance"].currency == "USD"
            assert (await reader.list_webhook_events()).status_code == 200
        truth = (await controls.get("/_mock/ground-truth")).json()
    assert len(truth["payout_items"]) == 1
    assert truth["balances"]["available"]["value"] == "1500.00"


@pytest.mark.asyncio
async def test_mock_dispute_reads_evidence_controls_and_owner_claim():
    app = create_app(control_enabled=True)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as controls:
        seed = {
            "balances": {"available": "1500.00"},
            "captures": [{"id": "CAP1", "order_id": "ORD1", "amount": "400.00"}],
            "trackers": [
                {"transaction_id": "CAP1", "status": "DELIVERED", "shipment_uploader": "MERCHANT"}
            ],
        }
        assert (await controls.post("/_mock/seed", json=seed)).status_code == 200
        dispute = (
            await controls.post(
                "/_mock/disputes",
                json={
                    "capture_id": "CAP1",
                    "amount": "400.00",
                    "reason": "MERCHANDISE_OR_SERVICE_NOT_RECEIVED",
                },
            )
        ).json()
        id = dispute["dispute_id"]
        target = {"paypal_dispute_id": id}
        async with PayPalHTTP(PayPalSettings(base_url=BASE), transport=transport) as http:
            writer, reader = PayPalWrite(http), PayPalRead(http)
            assert (await reader.get_capture("CAP1")).amounts["/amount"] == Money(40000, "USD")
            assert (await reader.get_order("ORD1")).amounts["/purchase_units/0/amount"] == Money(
                40000, "USD"
            )
            tracking = await reader.get_trackers("CAP1")
            assert tracking.body["trackers"][0]["shipment_uploader"] == "MERCHANT"
            assert len((await reader.list_disputes()).body["items"]) == 1
            full = await reader.get_dispute(id)
            assert full.amounts["/dispute_amount"] == Money(40000, "USD")
            evidence = persisted(
                "DISPUTE_EVIDENCE",
                body={
                    "evidences": [
                        {
                            "evidence_type": "PROOF_OF_FULFILLMENT",
                            "notes": "Merchant uploaded tracking",
                        }
                    ]
                },
                target=target,
            )
            sent = await writer.provide_evidence(evidence)
            assert isinstance(sent, Ok) and sent.resp.body["status"] == "UNDER_REVIEW"
            staged = SandboxControls(http)
            assert (
                await staged.require_evidence(id, {"action": "SELLER_EVIDENCE"})
            ).status_code == 200
            assert isinstance(await writer.provide_evidence(evidence), Ok)
            won = await staged.adjudicate(id, {"adjudication_outcome": "SELLER_FAVOR"})
            assert won.body["dispute_outcome"]["outcome_code"] == "RESOLVED_SELLER_FAVOUR"
            other = (
                await controls.post(
                    "/_mock/disputes",
                    json={
                        "capture_id": "CAP1",
                        "amount": "60.00",
                        "reason": "MERCHANDISE_OR_SERVICE_NOT_AS_DESCRIBED",
                    },
                )
            ).json()
            accept = persisted(
                "DISPUTE_ACCEPT_CLAIM",
                body={"accept_claim_type": "REFUND", "note": "Owner approved"},
                target={"paypal_dispute_id": other["dispute_id"]},
            )
            accepted = await writer.accept_claim(accept)
            assert isinstance(accepted, Ok)
            assert accepted.resp.body["dispute_outcome"]["amount_refunded"]["value"] == "60.00"


@pytest.mark.asyncio
async def test_mock_signed_real_event_and_forged_signature():
    app = create_app(control_enabled=True)
    transport = httpx.ASGITransport(app=app)
    async with PayPalHTTP(PayPalSettings(base_url=BASE), transport=transport) as http:
        registration = await http.request(
            "POST",
            "/v1/notifications/webhooks",
            json={
                "url": "http://localhost:8000/webhooks/paypal",
                "event_types": [{"name": "*"}],
            },
        )
        id = registration.body["id"]
        service = app.state.webhook_service
        event = service.emit("CUSTOMER.DISPUTE.CREATED", {"dispute_id": "PP-D-1"})
        record = service.store.webhooks.events[event["id"]]
        headers = service.headers(record, id)
        verifier = WebhookVerifier(http, id)
        assert await verifier.verify(record["raw"], headers) == Verification.SUCCESS
        bad = {**headers, "paypal-transmission-sig": "invalid"}
        assert await verifier.verify(record["raw"], bad) == Verification.FAILURE
        assert (
            await verifier.verify(record["raw"].replace(b"PP-D-1", b"PP-D-2"), headers)
            == Verification.FAILURE
        )


@pytest.mark.asyncio
async def test_mock_lost_response_after_commit_identical_replay_one_item():
    app = create_app(control_enabled=True)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as controls:
        await controls.post("/_mock/seed", json={"balances": {"available": "1500.00"}})
        injected = await controls.post(
            "/_mock/faults",
            json={
                "match": {"method": "POST", "path": "^/v1/payments/payouts$"},
                "action": {"type": "drop_response", "after_commit": True},
            },
        )
        assert injected.status_code == 201
        async with PayPalHTTP(PayPalSettings(base_url=BASE), transport=transport) as http:
            writer = PayPalWrite(http)
            row = persisted()
            result = await writer.create_payout_single_item(row)
            assert isinstance(result, Ambiguous)
            assert len((await controls.get("/_mock/ground-truth")).json()["payout_items"]) == 1
            row["state"] = "UNKNOWN"
            recovered = await writer.create_payout_single_item(row)
            assert isinstance(recovered, Ok)
        truth = (await controls.get("/_mock/ground-truth")).json()
    assert len(truth["payout_items"]) == 1
    assert truth["balances"]["available"]["value"] == "1499.00"


@pytest.mark.asyncio
async def test_mock_concurrent_duplicate_keeps_uncertainty_and_one_item():
    app = create_app(control_enabled=True)
    app.state.store.payouts.available_minor = 1000
    async with PayPalHTTP(
        PayPalSettings(base_url=BASE), transport=httpx.ASGITransport(app=app)
    ) as http:
        writer = PayPalWrite(http)
        results = await asyncio.gather(
            *(writer.create_payout_single_item(persisted()) for _ in range(2))
        )
    assert sorted(type(item).__name__ for item in results) == ["Ambiguous", "Ok"]
    assert len(app.state.store.payouts.items) == 1


@pytest.mark.skip(reason="C09 has not published today's redacted sandbox recordings and INDEX")
def test_published_sandbox_recordings_contract():
    """C09 owner fills the request/response contract cases after publishing fixtures."""
