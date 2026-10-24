"""C24: pure rules fail closed and keep evidence autonomy independent of stage."""

from dataclasses import replace
from datetime import timedelta

import pytest
from shield_world import DISPUTE_A, NOW, ORDER_A, cash, usd

from countersign.shield.engine import Outcome, Verdict, evaluate
from countersign.shield.rules import deadline_guard, evidence_integrity, refund_policy
from countersign.shield.snapshot import TIER, Action, Dispute, Proposal, Snapshot, Targets, Tier


def facts():
    return Snapshot(
        NOW,
        cash(),
        targets=Targets(dispute_ids=(DISPUTE_A,)),
        disputes=(
            Dispute(
                DISPUTE_A,
                ORDER_A,
                "ACTIVE",
                1,
                status="WAITING_FOR_SELLER_RESPONSE",
                amount=usd(400),
                seller_response_due_date=NOW + timedelta(days=2),
                links=(
                    {"rel": "provide_evidence", "href": "/provide-evidence", "method": "POST"},
                    {"rel": "accept_claim", "href": "/accept-claim", "method": "POST"},
                ),
                paypal_dispute_id="PP-D-TEST",
                seller_transaction_ids=("CAP400",),
            ),
        ),
        policies={
            "deadline_close_seconds": 3600,
            "refund_limit_per_order": {"minor": 50000, "currency": "USD"},
            "refund_limit_per_day": {"minor": 80000, "currency": "USD"},
        },
        orders=(
            {
                "id": ORDER_A,
                "paypal_order_id": "ORD400",
                "paypal_capture_id": "CAP400",
                "amount_minor": 40000,
                "currency": "USD",
                "tracking": {
                    "carrier": "USPS",
                    "tracking_number": "TRACK400",
                    "tracking_number_validated": False,
                },
            },
        ),
        evidence_refs=(f"order:{ORDER_A}", "tracking:TRACK400"),
        refund_totals={"orders": {}, "day": {}},
    )


def proposal(action=Action.PROVIDE_EVIDENCE, **params):
    return Proposal("proposal", action, {"paypal_dispute_id": "PP-D-TEST", **params}, "hash")


def change_dispute(snap, **kwargs):
    return replace(snap, disputes=(replace(snap.disputes[0], **kwargs),))


def test_evidence_autonomous_with_observed_chargeback_condition():
    snap = facts()
    decision = evaluate(proposal(), snap)
    assert TIER[Action.PROVIDE_EVIDENCE] is Tier.AUTONOMOUS
    assert decision.verdict is Verdict.ALLOW
    assert "not carrier-confirmed" in evidence_integrity.check(proposal(), snap).detail


@pytest.mark.parametrize(
    "field,value",
    [
        ("links", ()),
        ("status", "UNDER_REVIEW"),
        ("order_id", None),
        ("seller_transaction_ids", ("ANOTHER_CAPTURE",)),
        ("amount", usd(399)),
    ],
)
def test_evidence_rejects_wrong_or_missing_dispute_facts(field, value):
    snap = change_dispute(facts(), **{field: value})
    assert evidence_integrity.check(proposal(), snap).outcome is Outcome.BLOCK


@pytest.mark.parametrize(
    "params",
    [
        {"order_id": "other"},
        {"notes": "Buyer says delivered"},
        {"evidences": [{"notes": "The model claims delivery"}]},
    ],
)
def test_evidence_rejects_another_order_or_model_prose(params):
    assert evidence_integrity.check(proposal(**params), facts()).outcome is Outcome.BLOCK


@pytest.mark.parametrize(
    "refs", [(), ("tracking:INVENTED",), ("buyer:delivered",), (f"order:{ORDER_A}",)]
)
def test_only_stored_tracking_references_are_accepted(refs):
    assert (
        evidence_integrity.check(proposal(), replace(facts(), evidence_refs=refs)).outcome
        is Outcome.BLOCK
    )


def test_carrier_confirmation_requires_literal_true():
    snap = facts()
    order = {
        **snap.orders[0],
        "tracking": {**snap.orders[0]["tracking"], "tracking_number_validated": True},
    }
    result = evidence_integrity.check(proposal(), replace(snap, orders=(order,)))
    assert result.outcome is Outcome.PASS and result.facts["tracking_number_validated"] is True


@pytest.mark.parametrize("amount", [1, 40000])
def test_refund_always_requires_owner(amount):
    result = refund_policy.check(
        proposal(Action.ACCEPT_CLAIM, amount={"minor": amount, "currency": "USD"}), facts()
    )
    assert result.outcome is Outcome.NEEDS_OWNER


@pytest.mark.parametrize("amount", [40001, -1, True, 1.5])
def test_refund_over_dispute_or_invalid_money_is_blocked(amount):
    result = refund_policy.check(
        proposal(Action.ACCEPT_CLAIM, amount={"minor": amount, "currency": "USD"}), facts()
    )
    assert result.outcome is Outcome.BLOCK


@pytest.mark.parametrize("which", ["order", "day"])
def test_refund_aggregates_are_enforced(which):
    totals = {
        "orders": {f"{ORDER_A}:USD": 10001} if which == "order" else {},
        "day": {"USD": 40001} if which == "day" else {},
    }
    assert (
        refund_policy.check(
            proposal(Action.ACCEPT_CLAIM), replace(facts(), refund_totals=totals)
        ).outcome
        is Outcome.BLOCK
    )


@pytest.mark.parametrize("key", ["refund_limit_per_order", "refund_limit_per_day"])
def test_missing_refund_policy_is_blocked(key):
    snap = facts()
    assert (
        refund_policy.check(
            proposal(Action.ACCEPT_CLAIM),
            replace(snap, policies={k: v for k, v in snap.policies.items() if k != key}),
        ).outcome
        is Outcome.BLOCK
    )


@pytest.mark.parametrize("due", [None, NOW, NOW - timedelta(seconds=1)])
def test_missing_or_expired_deadline_is_blocked(due):
    assert (
        deadline_guard.check(
            proposal(), change_dispute(facts(), seller_response_due_date=due)
        ).outcome
        is Outcome.BLOCK
    )


def test_close_deadline_escalates_owner_but_keeps_valid_evidence_autonomous():
    snap = change_dispute(facts(), seller_response_due_date=NOW + timedelta(seconds=3600))
    assert deadline_guard.check(proposal(Action.ACCEPT_CLAIM), snap).outcome is Outcome.NEEDS_OWNER
    assert evaluate(proposal(), snap).verdict is Verdict.ALLOW


@pytest.mark.parametrize("close", [None, 0, -1, True, 1.5])
def test_unknown_close_policy_never_allows_evidence(close):
    snap = replace(facts(), policies={"deadline_close_seconds": close})
    assert evaluate(proposal(), snap).verdict is Verdict.NEEDS_OWNER


def test_missing_refund_history_blocks_and_read_only_actions_stay_available():
    assert (
        refund_policy.check(
            proposal(Action.ACCEPT_CLAIM), replace(facts(), refund_totals={})
        ).outcome
        is Outcome.BLOCK
    )
    assert (
        evaluate(proposal(Action.REQUEST_OWNER), replace(facts(), policies={})).verdict
        is Verdict.ALLOW
    )


@pytest.mark.parametrize(
    "tracking",
    [
        {"carrier": "USPS", "tracking_number": "TRACK400", "transaction_id": "OTHER-CAPTURE"},
        {"carrier": "USPS", "number": "OTHER", "tracking_number": "TRACK400"},
        {"carrier": "USPS", "tracking_number": " "},
    ],
)
def test_evidence_rejects_conflicting_stored_tracking(tracking):
    snap = facts()
    order = {**snap.orders[0], "tracking": tracking}
    assert (
        evidence_integrity.check(proposal(), replace(snap, orders=(order,))).outcome
        is Outcome.BLOCK
    )
