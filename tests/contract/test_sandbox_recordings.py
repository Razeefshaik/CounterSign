"""Our adapter classifies every recorded sandbox write response (C09 corpus).

The mock's replay tests compare the mock with these recordings; this file feeds the
recorded PayPal responses to PayPalWrite itself, through a fake transport with fake
credentials. Each recorded write is listed with the outcome it must produce, so a
newly published write recording fails until someone classifies it.
"""

import json
import re
from pathlib import Path

import httpx
import pytest

from countersign.config import PayPalSettings
from countersign.paypal.http import Ambiguous, DefiniteError, Ok, PayPalHTTP, canonical_payload_hash
from countersign.paypal.write import PayPalWrite

KEY = "cs-019a12de-7598-7000-8000-000000000002-a1"
RECORDINGS = Path(__file__).resolve().parents[2] / "recordings/sandbox"
SANDBOX = "https://api-m.sandbox.paypal.com"
WRITE = re.compile(
    r"/v1/payments/payouts"
    r"|/v1/payments/payouts-item/(?P<item>[A-Z0-9]+)/cancel"
    r"|/v1/customer/disputes/(?P<dispute>PP-[A-Z0-9-]+)/(?P<action>provide-evidence|accept-claim)"
)

# D6: every duplicate is a 400 linking the original batch, never a replayed 201.
DUPLICATE = "duplicate"
EXPECTED = {
    "2026-10-23/D3/04_maker_a.json": Ok,
    "2026-10-24/D4/04_unreg_1.json": Ok,
    "2026-10-24/D4/05_unreg_2.json": Ok,
    "2026-10-24/D4/06_unreg_3.json": Ok,
    "2026-10-24/D5/05_cancel_unreg_1.json": Ok,
    "2026-10-24/D5/08_cancel_again.json": DefiniteError,
    "2026-10-24/D5/10_cancel_success.json": DefiniteError,
    "2026-10-24/D6/04_a_base.json": Ok,
    "2026-10-24/D6/05_a_replay.json": DUPLICATE,
    "2026-10-24/D6/10_b_base.json": Ok,
    "2026-10-24/D6/11_b_new_request.json": DUPLICATE,
    "2026-10-24/D6/16_c_base.json": Ok,
    "2026-10-24/D6/17_c_changed_body.json": DUPLICATE,
    "2026-10-24/D6/22_d_concurrent.json": Ok,
    "2026-10-24/D6/23_d_concurrent.json": DUPLICATE,
    "2026-10-24/D6/29_e_lost_response.json": Ambiguous,
    "2026-10-24/D6/30_e_replay.json": DUPLICATE,
    "2026-10-24/D7/10_cancel_errpyo007.json": DefiniteError,
    "2026-10-24/D7/11_cancel_errpyo008.json": DefiniteError,
    "2026-10-24/D7/12_cancel_errpyo009.json": DefiniteError,
    "2026-10-24/D7/13_create_errpyo005.json": DefiniteError,
    "2026-10-24/D7/14_create_errpyo006.json": Ambiguous,
    "2026-10-24/D7/15_create_errpyo014.json": DUPLICATE,
    "2026-10-24/D7/16_create_errpyo036.json": Ambiguous,
    "2026-10-24/D7/17_create_errpyo039.json": Ambiguous,
    "2026-10-24/D10/09_snad_60_accept_claim_json.json": Ok,
    "2026-10-24/D10/14_spare_25_evidence_multipart.json": Ok,
}


def recorded_writes():
    found = {}
    for file in sorted(RECORDINGS.glob("*/*/[0-9]*_*.json")):
        exchange = json.loads(file.read_text(encoding="utf-8"))
        if exchange.get("method") == "POST" and WRITE.fullmatch(exchange.get("path", "")):
            found[file.relative_to(RECORDINGS).as_posix()] = exchange
    return found


WRITES = recorded_writes()


def test_every_recorded_write_has_an_expected_outcome():
    assert sorted(WRITES) == sorted(EXPECTED)


def persisted(kind="PAYOUT_CREATE", *, body=None, target=None):
    if body is None:
        body = {
            "sender_batch_header": {"sender_batch_id": KEY},
            "items": [
                {
                    "recipient_type": "EMAIL",
                    "receiver": "fake-maker@example.test",
                    "amount": {"value": "1.00", "currency": "USD"},
                    "sender_item_id": KEY + "-i1",
                }
            ],
        }
    payload = {"body": body, "request_id": KEY, **(target or {})}
    return {
        "id": KEY[3:-3],
        "kind": kind,
        "state": "IN_FLIGHT",
        "payload": payload,
        "payload_hash": canonical_payload_hash(payload),
    }


def operation(path):
    match = WRITE.fullmatch(path)
    if match["item"]:
        return "cancel_item", persisted("PAYOUT_CANCEL", body={}, target={
            "paypal_item_id": match["item"]
        })
    if match["dispute"]:
        if match["action"] == "accept-claim":
            body, kind, method = {"accept_claim_type": "REFUND"}, "DISPUTE_ACCEPT_CLAIM", "accept_claim"
        else:
            body = {"evidences": [{"evidence_type": "PROOF_OF_FULFILLMENT"}]}
            kind, method = "DISPUTE_EVIDENCE", "provide_evidence"
        return method, persisted(kind, body=body, target={"paypal_dispute_id": match["dispute"]})
    return "create_payout_single_item", persisted()


@pytest.mark.asyncio
@pytest.mark.parametrize("name", sorted(EXPECTED))
async def test_adapter_classifies_recorded_write(name):
    exchange = WRITES[name]
    sent = []

    def respond(request):
        if request.url.path == "/v1/oauth2/token":
            return httpx.Response(
                200, json={"access_token": "fake-token", "token_type": "Bearer", "expires_in": 3600}
            )
        sent.append((request.method, request.url.path))
        if exchange["response_status"] is None:
            raise httpx.ReadTimeout("recorded lost response", request=request)
        return httpx.Response(exchange["response_status"], json=exchange["response_body"])

    settings = PayPalSettings(base_url=SANDBOX, client_id="fake-client", client_secret="fake-secret")
    method, row = operation(exchange["path"])
    async with PayPalHTTP(settings, transport=httpx.MockTransport(respond)) as http:
        result = await getattr(PayPalWrite(http), method)(row)

    assert sent and set(sent) == {("POST", exchange["path"])}
    expected = EXPECTED[name]
    if expected is DUPLICATE:
        (original,) = [
            link["href"]
            for detail in exchange["response_body"]["details"]
            for link in detail.get("link", [])
        ]
        assert isinstance(result, Ambiguous)
        # The api.sandbox alias is normalised onto the configured origin.
        assert result.original_batch_link == SANDBOX + original.split(".paypal.com", 1)[1]
    else:
        assert type(result) is expected
