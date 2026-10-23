import json

import pytest
from fastapi.testclient import TestClient
from test_payouts import advance, authenticated, body, create

from mock_paypal.app import create_app
from mock_paypal.resources import ResourceBehavior

SEED = {
    "balances": {"available": "1500.00", "withheld": "0.00", "currency_code": "USD"},
    "registered_receivers": ["maker@example.com"],
    "captures": [
        {
            "id": "MOCKCAP400",
            "order_id": "MOCKORD1042",
            "amount": "400.00",
            "create_time": "2026-11-02T09:00:00Z",
        }
    ],
    "trackers": [
        {
            "transaction_id": "MOCKCAP400",
            "tracking_number": "9400000",
            "status": "DELIVERED",
            "carrier": "USPS",
            "shipment_uploader": "MERCHANT",
            "tracking_number_validated": True,
            "last_updated_time": "2026-11-02T09:00:00Z",
            "links": [],
        }
    ],
}


@pytest.fixture
def client():
    with TestClient(create_app(control_enabled=True)) as client:
        authenticated(client)
        assert client.post("/_mock/seed", json=SEED).status_code == 200
        yield client


def staged(client):
    response = client.post(
        "/_mock/disputes",
        json={
            "capture_id": "MOCKCAP400",
            "amount": "400.00",
            "reason": "MERCHANDISE_OR_SERVICE_NOT_RECEIVED",
            "messages": ["I did not receive it."],
        },
    )
    assert response.status_code == 200
    return response.json()


def truth(client):
    return client.get("/_mock/ground-truth").json()


def test_seed_orders_captures_trackers(client):
    assert (
        client.get("/v2/checkout/orders/MOCKORD1042").json()["purchase_units"][0][
            "amount"
        ]["value"]
        == "400.00"
    )
    assert (
        client.get("/v2/payments/captures/MOCKCAP400").json()[
            "seller_receivable_breakdown"
        ]["paypal_fee"]["value"]
        == "0.00"
    )
    assert (
        client.get("/v1/shipping/trackers?transaction_id=MOCKCAP400").json()[
            "trackers"
        ][0]["shipment_uploader"]
        == "MERCHANT"
    )
    assert client.get("/v2/checkout/orders/missing").status_code == 404


def test_hold_and_snapshot_refresh(client):
    first = client.get("/v1/reporting/balances").json()
    d = staged(client)
    assert truth(client)["balances"]["available"]["value"] == "1100.00"
    assert truth(client)["balances"]["withheld"]["value"] == "400.00"
    assert client.get("/v1/reporting/balances").json() == first
    advance(client, 10799)
    assert client.get("/v1/reporting/balances").json() == first
    advance(client, 1)
    fresh = client.get("/v1/reporting/balances").json()
    assert fresh["balances"][0]["available_balance"]["value"] == "1100.00"
    assert fresh["last_refresh_time"] == "2026-11-02T12:00:00Z"
    assert (
        client.get("/v1/reporting/balances?as_of_time=2026-11-02T09:00:00Z").json()
        == first
    )
    assert client.get("/v1/customer/disputes/" + d["dispute_id"]).status_code == 200


def test_dispute_pagination_filters_and_buyer_message(client):
    first = staged(client)
    staged(client)
    response = client.get(
        "/v1/customer/disputes?page_size=1&disputed_transaction_id=MOCKCAP400"
    ).json()
    assert len(response["items"]) == 1
    second = client.get(response["links"][0]["href"]).json()
    assert len(second["items"]) == 1
    assert second["items"][0]["dispute_id"] != response["items"][0]["dispute_id"]
    assert (
        client.get("/v1/customer/disputes?dispute_state=RESOLVED").json()["items"] == []
    )
    path = "/_mock/disputes/" + first["dispute_id"]
    assert (
        client.post(
            path + "/buyer-message", json={"content": "Please check tracking"}
        ).json()["messages"][-1]["posted_by"]
        == "BUYER"
    )
    assert client.get("/v1/customer/disputes?next_page_token=bad").status_code == 422


def test_evidence_multipart_hateoas_and_seller_win(client):
    d = staged(client)
    path = "/v1/customer/disputes/" + d["dispute_id"]
    assert "provide_evidence" in [link["rel"] for link in d["links"]]
    assert (
        client.post(path + "/provide-evidence", json={"evidences": []}).status_code
        == 422
    )
    response = client.post(
        path + "/provide-evidence",
        files={
            "input": (
                None,
                json.dumps(
                    {
                        "evidences": [
                            {
                                "evidence_type": "PROOF_OF_FULFILLMENT",
                                "notes": "Merchant tracking",
                            }
                        ]
                    }
                ),
                "application/json",
            )
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == "UNDER_REVIEW"
    assert "provide_evidence" not in [link["rel"] for link in response.json()["links"]]
    assert (
        client.post(
            path + "/provide-evidence", files={"input": (None, "{}")}
        ).status_code
        == 422
    )
    result = client.post(
        path + "/adjudicate", json={"adjudication_outcome": "SELLER_FAVOR"}
    )
    assert result.json()["dispute_outcome"]["outcome_code"] == "RESOLVED_SELLER_FAVOUR"
    assert truth(client)["balances"]["available"]["value"] == "1500.00"
    assert truth(client)["balances"]["withheld"]["value"] == "0.00"
    assert [link["rel"] for link in result.json()["links"]] == ["self"]
    assert (
        client.post(
            path + "/accept-claim", json={"accept_claim_type": "REFUND"}
        ).status_code
        == 422
    )


@pytest.mark.parametrize("hold", [True, False])
def test_accept_claim_refund_with_or_without_hold(client, hold):
    client.post("/_mock/config", json={"dispute_hold": hold})
    d = staged(client)
    response = client.post(
        "/v1/customer/disputes/" + d["dispute_id"] + "/accept-claim",
        json={"accept_claim_type": "REFUND", "note": "Owner approved"},
    )
    assert response.status_code == 200
    assert response.json()["dispute_outcome"]["amount_refunded"]["value"] == "400.00"
    actual = truth(client)
    assert len(actual["refunds"]) == 1
    assert actual["refunds"][0]["via"] == "accept_claim"
    assert actual["balances"]["available"]["value"] == "1100.00"
    assert actual["balances"]["withheld"]["value"] == "0.00"


def test_escalate_require_evidence_buyer_win(client):
    d = staged(client)
    path = "/v1/customer/disputes/" + d["dispute_id"]
    assert (
        client.post(
            path + "/require-evidence", json={"action": "SELLER_EVIDENCE"}
        ).status_code
        == 422
    )
    assert (
        client.post(path + "/escalate", json={"note": "Review"}).json()["status"]
        == "UNDER_REVIEW"
    )
    response = client.post(
        path + "/require-evidence", json={"action": "BUYER_EVIDENCE"}
    )
    assert response.json()["status"] == "WAITING_FOR_BUYER_RESPONSE"
    client.post(
        "/_mock/disputes/" + d["dispute_id"] + "/transition",
        json={"status": "UNDER_REVIEW", "stage": "CHARGEBACK"},
    )
    response = client.post(
        path + "/adjudicate", json={"adjudication_outcome": "BUYER_FAVOR"}
    )
    assert response.json()["dispute_outcome"]["outcome_code"] == "RESOLVED_BUYER_FAVOUR"
    assert truth(client)["refunds"][0]["via"] == "adjudicate"


def test_buyer_close_releases_hold_once(client):
    d = staged(client)
    path = "/_mock/disputes/" + d["dispute_id"] + "/buyer-close"
    assert (
        client.post(path, json={}).json()["dispute_outcome"]["outcome_code"]
        == "CANCELED_BY_BUYER"
    )
    assert client.post(path, json={}).status_code == 422
    assert truth(client)["balances"]["available"]["value"] == "1500.00"


def test_transactions_lag_and_duplicate_ids(client):
    create(client, body(value="1.00"))
    assert client.get("/v1/reporting/transactions").json()["transaction_details"] == []
    advance(client, 10800)
    response = client.get("/v1/reporting/transactions").json()
    assert response["total_items"] == 2
    assert any(
        t["transaction_info"]["transaction_amount"]["value"] == "-1.00"
        for t in response["transaction_details"]
    )
    seed = {**SEED, "repeat_transaction_ids": True}
    assert client.post("/_mock/seed", json=seed).status_code == 200
    create(client, body(value="1.00"))
    advance(client, 10800)
    records = client.get(
        "/v1/reporting/transactions?transaction_id=MOCKT-REPEATED"
    ).json()["transaction_details"]
    assert len(records) == 2


def test_seed_existing_unclaimed_and_invalid_seed_is_atomic(client):
    seed = {
        **SEED,
        "payout_items": [
            {
                "payout_item_id": "MOCKI-OLD",
                "receiver": "typo@example.com",
                "amount": "300.00",
                "status": "UNCLAIMED",
                "created_at": "2026-11-01T09:00:00Z",
            }
        ],
    }
    assert client.post("/_mock/seed", json=seed).status_code == 200
    assert truth(client)["balances"]["available"]["value"] == "1500.00"
    assert (
        client.post(
            "/_mock/seed", json={**seed, "balances": {"available": "1.123"}}
        ).status_code
        == 422
    )
    assert truth(client)["payout_items"][0]["payout_item_id"] == "MOCKI-OLD"
    assert (
        client.post("/v1/payments/payouts-item/MOCKI-OLD/cancel").json()[
            "transaction_status"
        ]
        == "RETURNED"
    )
    assert truth(client)["balances"]["available"]["value"] == "1800.00"


def test_pending_behavior_switches():
    app = create_app(
        control_enabled=True,
        resource_behavior=ResourceBehavior(
            initial_stage="CHARGEBACK",
            evidence_format="json",
            capture_transaction_field="capture_id",
        ),
    )
    with TestClient(app) as client:
        authenticated(client)
        client.post("/_mock/seed", json=SEED)
        d = staged(client)
        assert d["disputed_transactions"][0]["capture_id"] == "MOCKCAP400"
        assert (
            client.post(
                "/v1/customer/disputes/" + d["dispute_id"] + "/provide-evidence",
                json={"evidences": [{"notes": "Proof"}]},
            ).status_code
            == 200
        )
        client.post("/_mock/config", json={"evidence_allowed_stages": ["INQUIRY"]})
        d = staged(client)
        assert "provide_evidence" not in [link["rel"] for link in d["links"]]


def test_negative_available_minor_unit_is_reported_exactly(client):
    seed = {
        **SEED,
        "balances": {"available": "0.00", "withheld": "0.00", "currency_code": "USD"},
    }
    assert client.post("/_mock/seed", json=seed).status_code == 200
    response = client.post(
        "/_mock/disputes",
        json={"capture_id": "MOCKCAP400", "amount": "0.01", "reason": "TEST"},
    )
    assert response.status_code == 200
    assert truth(client)["balances"]["available"]["value"] == "-0.01"
    assert (
        client.post(
            "/_mock/disputes/" + response.json()["dispute_id"] + "/buyer-close", json={}
        ).status_code
        == 200
    )
    assert truth(client)["balances"]["available"]["value"] == "0.00"


@pytest.mark.parametrize("status", ["PENDING", "UNCLAIMED"])
def test_reseed_invalidates_old_item_timers(client, status):
    seed = {
        **SEED,
        "payout_items": [
            {
                "payout_item_id": "OLD",
                "receiver": "typo@example.com",
                "amount": "1.00",
                "status": status,
                "created_at": "2026-10-04T09:00:00Z",
            }
        ],
    }
    assert client.post("/_mock/seed", json=seed).status_code == 200
    old = client.app.state.store.payouts.items["OLD"]
    assert client.post("/_mock/seed", json=SEED).status_code == 200
    advance(client, 86400)
    assert truth(client)["balances"]["available"]["value"] == "1500.00"
    assert truth(client)["payout_items"] == []
    assert old.status == status
