"""Shared pure readers of C24 snapshot facts; no untrusted fact is authoritative."""

from collections.abc import Mapping


def dispute(proposal, snap):
    if len(snap.targets.dispute_ids) != 1:
        return None
    row = snap.dispute_by_id.get(snap.targets.dispute_ids[0])
    if row is None:
        return None
    if proposal.params.get("dispute_id", row.id) != row.id:
        return None
    if proposal.params.get("paypal_dispute_id", row.paypal_dispute_id) != row.paypal_dispute_id:
        return None
    return row


def has_link(row, rel):
    return any(
        isinstance(link, Mapping)
        and link.get("rel") == rel
        and link.get("method", "POST").upper() == "POST"
        and isinstance(link.get("href"), str)
        and link["href"]
        for link in row.links
    )


def policy_money(value, currency):
    if (
        not isinstance(value, Mapping)
        or set(value) != {"minor", "currency"}
        or type(value["minor"]) is not int
        or value["minor"] < 0
        or value["currency"] != currency
    ):
        return None
    return value["minor"]
