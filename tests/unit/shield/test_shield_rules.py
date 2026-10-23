"""One PASS and one BLOCK (and NEEDS_OWNER where the rule has it) per C17 slice rule."""

from dataclasses import replace
from datetime import timedelta

import pytest
from shield_world import (
    DISPUTE_A,
    EARMARK_A,
    EMAIL_B,
    EMAIL_C,
    LINE_B,
    LINE_C,
    LINES,
    MAKER_B,
    MAKER_C,
    MAKERS,
    NOW,
    OBL_A,
    OBL_B,
    OBL_C,
    OBLIGATIONS,
    OP_C1,
    RUN,
    attempt,
    cash,
    item,
    run_cash_record,
    usd,
    v7,
)

from countersign.fsm.attempt import UNKNOWN_REPLAY_WINDOW, AttemptState
from countersign.fsm.dispute import Phase
from countersign.fsm.line import LineState
from countersign.fsm.obligation import ObligationState
from countersign.fsm.operation import OperationState
from countersign.money import Money
from countersign.shield.engine import Outcome
from countersign.shield.rules import (
    approval_binding,
    cancel_before_replace,
    dispute_reserve,
    funding,
    idempotency,
    payee_integrity,
    payout_state_machine,
    settlement,
    unknown_scoped,
    write_ahead,
)
from countersign.shield.snapshot import Action, Dispute, Operation, Targets

S = AttemptState


def with_obligation(snap, obligation_id, **changes):
    return replace(
        snap,
        obligations=tuple(
            replace(o, **changes) if o.id == obligation_id else o for o in snap.obligations
        ),
    )


def with_line(snap, line_id, **changes):
    return replace(
        snap, lines=tuple(replace(x, **changes) if x.id == line_id else x for x in snap.lines)
    )


def outcome(rule, proposal, snap):
    result = rule.check(proposal, snap)
    assert result is not None and result.rule is rule.NAME
    return result


# SETTLEMENT


def test_settlement_passes_unsettled_obligations(world):
    result = outcome(settlement, world.proposal(), world.run())
    assert result.outcome is Outcome.PASS


def test_settlement_blocks_paying_an_already_settled_obligation(world):
    # HANDOFF live safety moment 3: a schema-valid proposal paying a SETTLED obligation.
    snap = with_obligation(world.run(), OBL_B, state=ObligationState.SETTLED)
    result = outcome(settlement, world.proposal(), snap)
    assert result.outcome is Outcome.BLOCK
    assert f"obligation {OBL_B} is already SETTLED" in result.detail


def test_settlement_blocks_an_obligation_in_a_live_attempt(world):
    snap = with_obligation(world.run(), OBL_B, state=ObligationState.IN_FLIGHT)
    snap = replace(snap, attempts=(*snap.attempts, attempt(LINE_B, 1, S.PENDING, id=v7(0x902))))
    result = outcome(settlement, world.proposal(), snap)
    assert result.outcome is Outcome.BLOCK
    assert "IN_FLIGHT" in result.detail and "still live (PENDING)" in result.detail


def test_settlement_blocks_paying_more_than_the_line_owes(world):
    snap = world.run(payouts=(item(LINE_B, 1, MAKER_B, usd(4860), EMAIL_B),))
    assert outcome(settlement, world.proposal(), snap).outcome is Outcome.BLOCK


def test_settlement_ignores_actions_that_pay_nobody(world):
    assert (
        settlement.check(world.proposal(Action.CANCEL_UNCLAIMED_PAYOUT), world.cancel_c()) is None
    )


# PAYOUT_STATE_MACHINE


def test_state_machine_passes_a_first_attempt_and_a_replacement(world):
    assert outcome(payout_state_machine, world.proposal(), world.run()).outcome is Outcome.PASS


@pytest.mark.parametrize("live", [S.PENDING, S.ONHOLD, S.BLOCKED, S.UNKNOWN])
def test_state_machine_blocks_a_new_attempt_while_the_previous_is_live(world, live):
    snap = world.run(attempts=(attempt(LINE_C, 1, live),))
    result = outcome(payout_state_machine, world.proposal(), snap)
    assert result.outcome is Outcome.BLOCK
    assert f"no new attempt while attempt 1 on line {LINE_C} is {live}" in result.detail


def test_state_machine_blocks_a_skipped_attempt_number(world):
    snap = world.run(payouts=(item(LINE_C, 3, MAKER_C, usd(300), EMAIL_C),))
    result = outcome(payout_state_machine, world.proposal(), snap)
    assert result.outcome is Outcome.BLOCK and "next attempt is 2, not 3" in result.detail


def test_state_machine_blocks_paying_a_void_obligation(world):
    snap = with_obligation(world.run(), OBL_B, state=ObligationState.VOID)
    assert outcome(payout_state_machine, world.proposal(), snap).outcome is Outcome.BLOCK


def test_cancel_passes_only_when_unclaimed(world):
    proposal = world.proposal(Action.CANCEL_UNCLAIMED_PAYOUT, params={"payout_item_id": "5KUD"})
    assert outcome(payout_state_machine, proposal, world.cancel_c()).outcome is Outcome.PASS
    result = outcome(payout_state_machine, proposal, world.cancel_c(S.SUCCESS))
    assert result.outcome is Outcome.BLOCK and "cancel only when UNCLAIMED" in result.detail


def test_defer_blocks_a_payout_already_sent(world):
    proposal = world.proposal(Action.DEFER_SCHEDULED_PAYOUT, params={"line_id": LINE_B})
    snap = world.run(payouts=(), targets=Targets(line_ids=(LINE_B,)))
    assert outcome(payout_state_machine, proposal, snap).outcome is Outcome.PASS
    snap = with_line(snap, LINE_B, state=LineState.PAYING)
    assert outcome(payout_state_machine, proposal, snap).outcome is Outcome.BLOCK


# CANCEL_BEFORE_REPLACE


@pytest.mark.parametrize("previous", [S.RETURNED, S.FAILED, S.REJECTED])
def test_replace_passes_after_a_confirmed_not_paid_attempt(world, previous):
    snap = world.run(attempts=(attempt(LINE_C, 1, previous),))
    assert outcome(cancel_before_replace, world.proposal(), snap).outcome is Outcome.PASS


@pytest.mark.parametrize("previous", [S.UNCLAIMED, S.PENDING, S.SUCCESS, S.REFUNDED])
def test_replace_blocks_until_get_shows_returned(world, previous):
    snap = world.run(attempts=(attempt(LINE_C, 1, previous),))
    result = outcome(cancel_before_replace, world.proposal(), snap)
    assert result.outcome is Outcome.BLOCK and "GET shows RETURNED" in result.detail


def test_replace_blocks_a_returned_status_no_get_confirmed(world):
    snap = world.run(attempts=(attempt(LINE_C, 1, S.RETURNED, checked=False),))
    result = outcome(cancel_before_replace, world.proposal(), snap)
    assert result.outcome is Outcome.BLOCK and "never confirmed by a GET" in result.detail


def test_replace_passes_when_there_is_no_replacement(world):
    result = outcome(cancel_before_replace, world.proposal(), world.run(payouts=(world.pay_b(),)))
    assert result.outcome is Outcome.PASS and result.detail == "no replacement attempt"


# IDEMPOTENCY


def test_keys_derive_from_line_id_and_attempt_number():
    keys = idempotency.derive_keys(LINE_B, 2)
    assert keys.sender_batch_id == keys.request_id == f"cs-{LINE_B}-a2"
    assert keys.sender_item_id == f"cs-{LINE_B}-a2-i1"


def test_idempotency_passes_derived_keys(world):
    assert outcome(idempotency, world.proposal(), world.run()).outcome is Outcome.PASS


def test_idempotency_blocks_keys_not_derived(world):
    snap = world.run(payouts=(replace(world.pay_b(), request_id="retry-123"),))
    result = outcome(idempotency, world.proposal(), snap)
    assert result.outcome is Outcome.BLOCK and "keys are not derived" in result.detail


def test_idempotency_blocks_a_line_id_that_is_not_uuid7(world):
    line_id = "0192f1c4-7a10-4c3e-9b1a-5d2f00000701"  # version 4
    snap = world.run(payouts=(item(line_id, 1, MAKER_B, usd(486), EMAIL_B),))
    assert outcome(idempotency, world.proposal(), snap).outcome is Outcome.BLOCK


def test_idempotency_blocks_reusing_an_attempt_for_another_payload(world):
    # Attempt 1 on line C exists (RETURNED); sending attempt 1 again reuses its keys.
    snap = world.run(payouts=(item(LINE_C, 1, MAKER_C, usd(300), EMAIL_C),))
    result = outcome(idempotency, world.proposal(), snap)
    assert result.outcome is Outcome.BLOCK and "never reused" in result.detail


def test_idempotency_passes_an_identical_replay_of_unknown_inside_the_window(world):
    unknown = attempt(LINE_C, 2, S.UNKNOWN, id=v7(0x902), email=EMAIL_C)
    snap = world.run(attempts=(attempt(LINE_C, 1, S.RETURNED), unknown))
    result = outcome(idempotency, world.proposal(), snap)
    assert result.outcome is Outcome.PASS and "identical replay" in result.detail


def test_idempotency_blocks_replacing_unknown_with_a_different_payload(world):
    unknown = attempt(LINE_C, 2, S.UNKNOWN, id=v7(0x902), email="other@example.com")
    snap = world.run(attempts=(attempt(LINE_C, 1, S.RETURNED), unknown))
    assert outcome(idempotency, world.proposal(), snap).outcome is Outcome.BLOCK


def test_idempotency_sends_an_unknown_past_25_days_to_the_owner(world):
    old = NOW - UNKNOWN_REPLAY_WINDOW - timedelta(minutes=1)
    unknown = attempt(LINE_C, 2, S.UNKNOWN, id=v7(0x902), email=EMAIL_C, prepared_at=old)
    snap = world.run(attempts=(attempt(LINE_C, 1, S.RETURNED), unknown))
    result = outcome(idempotency, world.proposal(), snap)
    assert result.outcome is Outcome.NEEDS_OWNER and "25-day replay window" in result.detail


# WRITE_AHEAD


def test_write_ahead_passes_a_pending_operation_with_the_same_hash(world):
    result = outcome(write_ahead, world.proposal(), world.executing())
    assert result.outcome is Outcome.PASS and result.detail == "operation recorded before call"


@pytest.mark.parametrize(
    "operation",
    [
        None,
        {"state": OperationState.IN_FLIGHT},
        {"payload_hash": "sha256:" + "0" * 64},
        {"kind": "PAYOUT_CANCEL"},
        {"proposal_id": v7(0x999)},
    ],
)
def test_write_ahead_blocks_without_a_matching_pending_operation(world, operation):
    snap = world.executing()
    snap = replace(
        snap, operation=None if operation is None else replace(snap.operation, **operation)
    )
    assert outcome(write_ahead, world.proposal(), snap).outcome is Outcome.BLOCK


def test_write_ahead_is_only_checked_at_execution(world):
    assert write_ahead.check(world.proposal(), world.run()) is None
    assert write_ahead.check(world.proposal(Action.RESERVE_EARNINGS), world.executing()) is None


# DISPUTE_RESERVE


def test_dispute_reserve_passes_undisputed_shares(world):
    assert outcome(dispute_reserve, world.proposal(), world.run()).outcome is Outcome.PASS


def test_dispute_reserve_blocks_paying_a_reserved_share(world):
    # Maker A's $360 share of the disputed $400 order is put on a line and paid: blocked.
    snap = with_obligation(world.run(), OBL_A, line_id=LINE_B)
    result = outcome(dispute_reserve, world.proposal(), snap)
    assert result.outcome is Outcome.BLOCK
    assert f"obligation {OBL_A} is RESERVED" in result.detail
    assert "active dispute earmark" in result.detail and DISPUTE_A in result.detail


def test_dispute_reserve_blocks_an_order_disputed_before_the_obligation_moved(world):
    snap = world.run(
        disputes=(
            *world.run().disputes,
            Dispute(v7(0x102), OBLIGATIONS[1].order_id, Phase.ACTIVE, 1),
        )
    )
    assert outcome(dispute_reserve, world.proposal(), snap).outcome is Outcome.BLOCK


@pytest.mark.parametrize(
    ("phase", "expected"),
    [
        (Phase.WON, Outcome.PASS),
        (Phase.CANCELLED, Outcome.PASS),
        (Phase.ACTIVE, Outcome.BLOCK),
        (Phase.UNKNOWN_OUTCOME, Outcome.BLOCK),
    ],
)
def test_release_reserve_only_once_the_dispute_ended(world, phase, expected):
    proposal = world.proposal(Action.RELEASE_RESERVE, params={"obligation_id": OBL_A})
    snap = world.run(
        payouts=(),
        targets=Targets(obligation_ids=(OBL_A,)),
        disputes=(Dispute(DISPUTE_A, OBLIGATIONS[0].order_id, phase, 2),),
    )
    assert outcome(dispute_reserve, proposal, snap).outcome is expected


# FUNDING


def test_funding_counts_the_runs_own_reservation_once(world):
    # unreserved = 1340 - 786 (this run's RUN_CASH) = 554 < 786 needed; with its own 786 it fits.
    snap = world.run()
    assert snap.cash.unreserved == usd(554)
    result = outcome(funding, world.proposal(), snap)
    assert result.outcome is Outcome.PASS
    assert result.facts == {"need": 78600, "unreserved": 55400, "own": 78600}


def test_funding_blocks_when_unreserved_cash_is_short(world):
    snap = world.run(cash=cash(available=600, run_cash=0), reservations=(EARMARK_A,))
    result = outcome(funding, world.proposal(), snap)
    assert result.outcome is Outcome.BLOCK
    assert "needs 786.00 USD but unreserved is 600.00 USD" in result.detail


def test_funding_ignores_cash_reserved_by_another_run(world):
    other = run_cash_record(run_id=v7(0x6FF))
    snap = world.run(reservations=(EARMARK_A, other))
    assert outcome(funding, world.proposal(), snap).outcome is Outcome.BLOCK


def test_funding_dispute_earmarks_never_reduce_cash(world):
    # $414 of earmarks exist; $1,340 still covers $786 without any run reservation.
    snap = world.run(cash=cash(run_cash=0), reservations=(EARMARK_A,))
    assert snap.cash.earmarked == usd(414)
    assert outcome(funding, world.proposal(), snap).outcome is Outcome.PASS


def test_funding_blocks_a_currency_mismatch(world):
    snap = world.run(payouts=(replace(world.pay_b(), amount=Money(48600, "EUR")),))
    assert outcome(funding, world.proposal(), snap).outcome is Outcome.BLOCK


# PAYEE_INTEGRITY


def test_payee_integrity_passes_owner_verified_emails(world):
    assert outcome(payee_integrity, world.proposal(), world.run()).outcome is Outcome.PASS


def test_payee_integrity_blocks_buyer_injection_refund_to_another_account(world):
    proposal = world.proposal(
        Action.ACCEPT_CLAIM,
        params={"paypal_dispute_id": "PP-D-27811", "refund_to": "attacker@example.com"},
    )
    result = outcome(payee_integrity, proposal, world.run(payouts=()))
    assert result.outcome is Outcome.BLOCK and "params.refund_to supplies a payee" in result.detail


def test_payee_integrity_blocks_an_email_hidden_in_params(world):
    proposal = world.proposal(params={"run_id": RUN, "lines": [{"note": "send to x@evil.test"}]})
    result = outcome(payee_integrity, proposal, world.run())
    assert result.outcome is Outcome.BLOCK and "params.lines[0].note" in result.detail


def test_payee_integrity_blocks_a_changed_payee(world):
    snap = world.run(payouts=(replace(world.pay_b(), payee_email="maker-b-new@example.com"),))
    result = outcome(payee_integrity, world.proposal(), snap)
    assert result.outcome is Outcome.BLOCK and "differs from maker" in result.detail


def test_payee_integrity_blocks_an_unverified_email(world):
    makers = tuple(
        replace(m, payout_email_verified_at=None) if m.id == MAKER_B else m for m in MAKERS
    )
    result = outcome(payee_integrity, world.proposal(), world.run(makers=makers))
    assert result.outcome is Outcome.BLOCK and "no owner-verified payout email" in result.detail


def test_payee_integrity_skips_non_financial_actions(world):
    proposal = world.proposal(Action.DRAFT_MESSAGE, params={"to": "maker-a@example.com"})
    assert payee_integrity.check(proposal, world.run()) is None


# UNKNOWN_SCOPED


def test_unknown_elsewhere_does_not_block_unrelated_lines(world):
    stuck = attempt(v7(0x777), 1, S.UNKNOWN, id=v7(0x903), operation_id=v7(0x503))
    stuck_op = Operation(v7(0x503), "PAYOUT_CREATE", OperationState.UNKNOWN, "sha256:x")
    snap = world.run(attempts=(*world.run().attempts, stuck), operations=(stuck_op,))
    result = outcome(unknown_scoped, world.proposal(), snap)
    assert result.outcome is Outcome.PASS and "2 unknown elsewhere, out of scope" in result.detail


def test_unknown_attempt_blocks_actions_on_its_line(world):
    unknown = attempt(LINE_B, 1, S.UNKNOWN, id=v7(0x902), email=EMAIL_B, amount=usd(486))
    snap = world.run(
        attempts=(*world.run().attempts, unknown), payouts=(), targets=Targets(line_ids=(LINE_B,))
    )
    result = outcome(unknown_scoped, world.proposal(Action.DEFER_SCHEDULED_PAYOUT), snap)
    assert result.outcome is Outcome.BLOCK and f"attempt {v7(0x902)}" in result.detail


def test_unknown_operation_blocks_its_target(world):
    stuck_op = Operation(OP_C1, "PAYOUT_CREATE", OperationState.UNKNOWN, "sha256:x")
    result = outcome(unknown_scoped, world.proposal(), world.run(operations=(stuck_op,)))
    assert result.outcome is Outcome.BLOCK and f"operation {OP_C1} is UNKNOWN" in result.detail


def test_unknown_dispute_outcome_blocks_its_obligation(world):
    proposal = world.proposal(Action.RELEASE_RESERVE, params={"obligation_id": OBL_A})
    snap = world.run(
        payouts=(),
        targets=Targets(obligation_ids=(OBL_A,)),
        disputes=(Dispute(DISPUTE_A, OBLIGATIONS[0].order_id, Phase.UNKNOWN_OUTCOME, 2),),
    )
    assert outcome(unknown_scoped, proposal, snap).outcome is Outcome.BLOCK


def test_unloaded_record_is_unknown(world):
    snap = world.run(lines=(LINES[0],))
    result = outcome(unknown_scoped, world.proposal(), snap)
    assert result.outcome is Outcome.BLOCK and f"line:{LINE_C} not loaded" in result.detail


def test_identical_replay_resolves_its_own_unknown(world):
    unknown = attempt(LINE_C, 2, S.UNKNOWN, id=v7(0x902), email=EMAIL_C, operation_id=v7(0x504))
    op = Operation(v7(0x504), "PAYOUT_CREATE", OperationState.UNKNOWN, "sha256:x", target_id=LINE_C)
    snap = world.run(attempts=(attempt(LINE_C, 1, S.RETURNED), unknown), operations=(op,))
    snap = replace(snap, payouts=(world.resend_c(),))
    assert outcome(unknown_scoped, world.proposal(), snap).outcome is Outcome.PASS


# APPROVAL_BINDING


def test_approval_binding_asks_the_owner_before_execution(world):
    result = outcome(approval_binding, world.proposal(), world.run())
    assert result.outcome is Outcome.NEEDS_OWNER
    assert result.facts["record_versions"] == {
        f"maker:{MAKER_B}": 2,
        f"maker:{MAKER_C}": 3,
        f"line:{LINE_B}": 1,
        f"line:{LINE_C}": 4,
        f"obligation:{OBL_B}": 3,
        f"obligation:{OBL_C}": 5,
    }


def test_approval_binding_passes_a_bound_approval_at_execution(world):
    assert outcome(approval_binding, world.proposal(), world.executing()).outcome is Outcome.PASS


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"used_at": NOW - timedelta(minutes=5)}, "already used"),
        ({"expires_at": NOW}, "expired"),
        ({"payload_hash": "sha256:" + "1" * 64}, "payload changed"),
        ({"proposal_id": v7(0x998)}, "another proposal"),
    ],
)
def test_approval_binding_blocks_a_used_expired_or_different_approval(world, change, message):
    snap = world.executing()
    snap = replace(snap, approval=replace(snap.approval, **change))
    result = outcome(approval_binding, world.proposal(), snap)
    assert result.outcome is Outcome.BLOCK and message in result.detail


def test_approval_binding_blocks_a_stale_record_version(world):
    snap = world.executing()
    snap = with_obligation(snap, OBL_B, version=4)  # e.g. a dispute opened, then was won
    result = outcome(approval_binding, world.proposal(), snap)
    assert result.outcome is Outcome.BLOCK
    assert result.detail.startswith("stale approval")
    assert f"obligation:{OBL_B} version 3 at approval, 4 now" in result.detail


def test_approval_binding_blocks_a_missing_approval_at_execution(world):
    snap = replace(world.executing(), approval=None)
    assert outcome(approval_binding, world.proposal(), snap).outcome is Outcome.BLOCK


def test_approval_binding_skips_autonomous_actions_without_approval(world):
    proposal = world.proposal(Action.CANCEL_UNCLAIMED_PAYOUT, params={"payout_item_id": "5KUD"})
    assert approval_binding.check(proposal, world.cancel_c()) is None
    assert approval_binding.check(proposal, replace(world.cancel_c(), at_execution=True)) is None


def test_payee_change_voids_the_approval(world):
    # The owner changes Maker B's email after approving: the maker version moves on.
    snap = world.executing()
    makers = tuple(replace(m, version=m.version + 1) if m.id == MAKER_B else m for m in MAKERS)
    result = outcome(approval_binding, world.proposal(), replace(snap, makers=makers))
    assert result.outcome is Outcome.BLOCK and f"maker:{MAKER_B} version 2" in result.detail
