"""Evidence is restricted to stored order/tracking facts for this exact dispute."""
from collections.abc import Mapping

from countersign.shield.engine import RuleName, blocked, passed
from countersign.shield.rules.facts import dispute, has_link
from countersign.shield.snapshot import Action

NAME = RuleName.EVIDENCE_INTEGRITY


def tracking_records(order):
    value = order.get("tracking")
    if isinstance(value, Mapping):
        value = value.get("trackers", [value])
    if not isinstance(value, (list, tuple)):
        return []
    return [row for row in value if isinstance(row, Mapping)]


def stored_evidence(order):
    """The executor can build this body from records, never from model prose."""
    info = []
    for row in tracking_records(order):
        carrier = row.get("carrier", row.get("carrier_name"))
        number = row.get("tracking_number", row.get("number"))
        if isinstance(carrier, str) and carrier and isinstance(number, str) and number:
            info.append({"carrier_name": carrier, "tracking_number": number})
    return [{"evidence_type": "PROOF_OF_FULFILLMENT",
             "evidence_info": {"tracking_info": info}}] if info else []


def check(proposal, snap):
    if proposal.action is not Action.PROVIDE_EVIDENCE:
        return None
    row = dispute(proposal, snap)
    if row is None or row.order_id is None or row.amount is None:
        return blocked(NAME, ["evidence dispute or linked order is unavailable"])
    if row.status != "WAITING_FOR_SELLER_RESPONSE" or not has_link(row, "provide_evidence"):
        return blocked(NAME, ["evidence requires WAITING_FOR_SELLER_RESPONSE and a provide_evidence link"])
    orders = [o for o in snap.orders if str(o.get("id")) == row.order_id]
    if len(orders) != 1:
        return blocked(NAME, ["dispute order is not held in system records"])
    order = orders[0]
    if (proposal.params.get("order_id", row.order_id) != row.order_id
            or order.get("paypal_capture_id") not in row.seller_transaction_ids
            or order.get("currency", "").strip() != row.amount.currency
            or order.get("amount_minor") != row.amount.minor):
        return blocked(NAME, ["evidence order, transaction or amount does not match this dispute"])
    allowed = {"dispute_id", "paypal_dispute_id", "order_id", "evidences"}
    if set(proposal.params) - allowed:
        return blocked(NAME, ["evidence contains fields outside stored record references"])
    evidences = stored_evidence(order)
    if not evidences:
        return blocked(NAME, ["stored tracking evidence is unavailable"])
    if "evidences" in proposal.params and proposal.params["evidences"] != evidences:
        return blocked(NAME, ["evidence content differs from stored tracking; free text is forbidden"])
    refs = {f"order:{row.order_id}", f"capture:{order['paypal_capture_id']}"}
    if order.get("paypal_order_id"):
        refs.add(f"order:{order['paypal_order_id']}")
    refs.update(f"tracking:{t['tracking_number']}"
                for t in evidences[0]["evidence_info"]["tracking_info"])
    if (not snap.evidence_refs or any(ref not in refs for ref in snap.evidence_refs)
            or not any(ref.startswith("tracking:") for ref in snap.evidence_refs)):
        return blocked(NAME, ["evidence must cite stored order/tracking records only"])
    validated = all(t.get("tracking_number_validated") is True for t in tracking_records(order))
    detail = ("merchant-uploaded tracking; carrier validated the tracking number"
              if validated else "merchant-uploaded tracking; not carrier-confirmed (tracking_number_validated is not true)")
    return passed(NAME, detail, tracking_number_validated=validated)
