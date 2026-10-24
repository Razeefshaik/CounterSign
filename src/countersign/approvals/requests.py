"""Exact non-payout requests built solely from locked, trusted application records."""

from countersign.config import PayPalBehavior
from countersign.money import Money
from countersign.paypal.http import resource_id
from countersign.shield.snapshot import Action

ACCEPT_NOTE = "Owner approved acceptance of this claim and refund of the disputed amount."


def relations(links):
    return sorted(
        {
            link["rel"].replace("-", "_")
            for link in links or []
            if isinstance(link, dict) and isinstance(link.get("rel"), str)
        }
    )


def build_request(row, action, data, targets, behavior: PayPalBehavior):
    """No params body, note, tracking or external ID is ever forwarded to PayPal."""
    request = {"request_id": "cs-" + str(row["id"]), "body": {}, "body_format": "none"}
    if action is Action.CANCEL_UNCLAIMED_PAYOUT:
        matches = [r for r in data["payout_attempts"] if str(r["id"]) == targets.get("attempt_id")]
        if len(matches) != 1 or not matches[0]["paypal_item_id"]:
            raise ValueError("CANCEL_TARGET_UNAVAILABLE")
        attempt = matches[0]
        if attempt["state"] != "UNCLAIMED":
            raise ValueError("CANCEL_REQUIRES_UNCLAIMED")
        request.update(
            target_type="payout_attempt",
            target_id=str(attempt["id"]),
            paypal_item_id=resource_id(attempt["paypal_item_id"]),
        )
        return request
    matches = [r for r in data["disputes"] if str(r["id"]) == targets.get("dispute_id")]
    if len(matches) != 1:
        raise ValueError("DISPUTE_TARGET_UNAVAILABLE")
    dispute = matches[0]
    if dispute["phase"] != "ACTIVE":
        raise ValueError("DISPUTE_NOT_ACTIVE")
    request.update(
        target_type="dispute",
        target_id=str(dispute["id"]),
        paypal_dispute_id=resource_id(dispute["paypal_dispute_id"]),
    )
    request["observation"] = {"status": dispute["status"], "relations": relations(dispute["links"])}
    if action is Action.PROVIDE_EVIDENCE:
        if dispute[
            "status"
        ] != "WAITING_FOR_SELLER_RESPONSE" or "provide_evidence" not in relations(dispute["links"]):
            raise ValueError("EVIDENCE_NOT_REQUESTED")
        orders = [r for r in data["orders"] if r["id"] == dispute["order_id"]]
        tracking = orders[0]["tracking"] if len(orders) == 1 else None
        if not isinstance(tracking, dict):
            raise ValueError("TRACKING_UNAVAILABLE")
        carrier, number = tracking.get("carrier"), tracking.get("number")
        if number is None:
            number = tracking.get("tracking_number")
        elif tracking.get("tracking_number", number) != number:
            raise ValueError("TRACKING_CONFLICT")
        if not all(isinstance(v, str) and v.strip() for v in (carrier, number)):
            raise ValueError("TRACKING_UNAVAILABLE")
        request.update(
            body_format=behavior.evidence_format,
            body={
                "evidences": [
                    {
                        "evidence_type": "PROOF_OF_FULFILLMENT",
                        "evidence_info": {
                            "tracking_info": [{"carrier_name": carrier, "tracking_number": number}]
                        },
                    }
                ]
            },
        )
    else:
        if "accept_claim" not in relations(dispute["links"]):
            raise ValueError("ACCEPT_CLAIM_NOT_AVAILABLE")
        if dispute["amount_refunded_minor"] not in (None, 0):
            raise ValueError("REFUND_ALREADY_OBSERVED")
        amount = Money(dispute["amount_minor"], dispute["currency"].strip())
        if amount.minor <= 0:
            raise ValueError("REFUND_AMOUNT_UNAVAILABLE")
        request.update(
            body_format=behavior.accept_claim_format,
            body={"note": ACCEPT_NOTE, "accept_claim_type": "REFUND"},
            refund_amount={"minor": amount.minor, "currency": amount.currency},
        )
    return request
