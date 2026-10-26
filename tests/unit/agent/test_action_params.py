"""Action syntax, prompt drift and bounded corrections without network or policy I/O."""

import json

import pytest

from countersign.agent.commander import Commander, CommanderError
from countersign.agent.models import ScriptedModel
from countersign.agent.params import (
    ACTION_PARAMS,
    EVIDENCE_PREFIXES,
    ProposalParamsError,
    validate_action_params,
)
from countersign.agent.prompts import SYSTEM_PROMPT
from countersign.shield.rules.payee_integrity import PAYEE_KEYS
from countersign.shield.snapshot import Action

EXAMPLES = {
    "WAIT": ({}, []),
    "DRAFT_MESSAGE": ({"message": "Your share remains reserved."}, []),
    "REQUEST_OWNER": ({"question": "Please review the claim."}, []),
    "PROVIDE_EVIDENCE": ({"paypal_dispute_id": "PP-D-1"}, ["capture:CAP1", "tracking:TRACK1"]),
    "ACCEPT_CLAIM": ({"paypal_dispute_id": "PP-D-1"}, ["dispute:PP-D-1"]),
    "CANCEL_UNCLAIMED_PAYOUT": ({"payout_item_id": "ITEM1"}, ["payout_item:ITEM1:status=UNCLAIMED"]),
    "CREATE_REPLACEMENT_PAYOUT": ({"line_id": "internal-line"}, []),
    "DEFER_SCHEDULED_PAYOUT": ({"line_id": "internal-line"}, []),
    "SCHEDULE_PAYOUT_RUN": ({"run_id": "internal-run"}, []),
    "RESERVE_EARNINGS": ({"obligation_id": "internal-obligation"}, []),
    "RELEASE_RESERVE": ({"obligation_id": "internal-obligation"}, []),
}


def proposal(action="PROVIDE_EVIDENCE", params=None, refs=None):
    return {"incident_id": "incident-1", "action": action,
            "params": params if params is not None else {},
            "evidence_refs": refs if refs is not None else ["tracking:TRACK1"],
            "rationale": "Use the observed stored records.",
            "expected_effect": {"available_change": {"minor": 0, "currency": "USD"}}}


class NoTools:
    schemas = ()

    async def invoke(self, *_):
        pytest.fail("proposal correction must not execute a tool")


def test_every_action_has_one_prompt_line_from_the_validator_table():
    assert set(ACTION_PARAMS) == set(Action) == set(EXAMPLES)
    for action, spec in ACTION_PARAMS.items():
        assert SYSTEM_PROMPT.splitlines().count(spec.prompt_line(action)) == 1
    assert "tracking:<number>" in SYSTEM_PROMPT
    assert "full dispute amount" in SYSTEM_PROMPT
    assert "available_change.minor is positive" in SYSTEM_PROMPT
    assert "refund_to" not in SYSTEM_PROMPT
    assert all(not set(spec.required + spec.optional) & PAYEE_KEYS for spec in ACTION_PARAMS.values())
    assert set(ACTION_PARAMS["ACCEPT_CLAIM"].policy_only) == PAYEE_KEYS
    expected = ("PROVIDE_EVIDENCE evidence_refs prefixes only: "
                + ", ".join(prefix + ":" for prefix in sorted(EVIDENCE_PREFIXES))
                + "; at least one tracking: reference is required.")
    assert SYSTEM_PROMPT.splitlines().count(expected) == 1


@pytest.mark.parametrize("action", EXAMPLES)
def test_documented_action_examples_are_accepted_as_shapes(action):
    validate_action_params(action, *EXAMPLES[action])


@pytest.mark.parametrize("action,params,refs", [
    ("PROVIDE_EVIDENCE", {}, ["tracking:TRACK1"]),
    ("ACCEPT_CLAIM", {}, []),
    ("ACCEPT_CLAIM", {"refund_to": "attacker@example.test"}, []),
    ("ACCEPT_CLAIM", {"dispute_id": "internal-dispute", "refund_to": "attacker@example.test"}, []),
    ("CANCEL_UNCLAIMED_PAYOUT", {}, []),
    ("CREATE_REPLACEMENT_PAYOUT", {}, []),
    ("DEFER_SCHEDULED_PAYOUT", {}, []),
    ("SCHEDULE_PAYOUT_RUN", {}, []),
    ("RESERVE_EARNINGS", {}, []),
    ("RELEASE_RESERVE", {}, []),
    ("PROVIDE_EVIDENCE", {"paypal_dispute_id": "PP-D-1"}, ["dispute:PP-D-1"]),
    ("PROVIDE_EVIDENCE", {"paypal_dispute_id": "PP-D-1"}, ["capture:CAP1"]),
    ("WAIT", {"unrecognized": "private"}, []),
    ("WAIT", {}, ["https://private.example"]),
    ("WAIT", {}, ["madeup:PRIVATE"]),
    ("WAIT", {}, ["dispute:"]),
    ("ACCEPT_CLAIM", {"paypal_dispute_id": None}, []),
    ("ACCEPT_CLAIM", {"paypal_dispute_id": "https://private.example"}, []),
    ("ACCEPT_CLAIM", {"paypal_dispute_id": "PP-D-1", "note": "untrusted"}, []),
    ("PROVIDE_EVIDENCE", {"paypal_dispute_id": "PP-D-1", "evidences": [{"notes": "untrusted"}]},
     ["tracking:TRACK1"]),
    ("SCHEDULE_PAYOUT_RUN", {"run_id": "run-1", "lines": [{"line_id": "line-1"}]}, []),
    ("SCHEDULE_PAYOUT_RUN", {"run_id": "run-1", "replacements": [{
        "line_id": "line-1", "maker_id": "maker-1", "amount": {"minor": 1, "currency": "USD"},
        "attempt_no": True}]}, []),
])
def test_missing_targets_unknown_keys_and_bad_reference_shapes_are_fixed_errors(action, params, refs):
    with pytest.raises(ProposalParamsError, match="^INVALID_PROPOSAL_PARAMS$") as caught:
        validate_action_params(action, params, refs)
    assert caught.value.action == action
    assert "private" not in str(caught.value)


def test_optional_stored_evidence_shape_does_not_judge_whether_records_match():
    validate_action_params("PROVIDE_EVIDENCE", {
        "paypal_dispute_id": "PP-D-1", "dispute_id": "internal-dispute", "order_id": "internal-order",
        "evidences": [{"evidence_type": "PROOF_OF_FULFILLMENT", "evidence_info": {
            "tracking_info": [{"carrier_name": "OTHER", "tracking_number": "NOT-STORED"}]}}],
    }, ["tracking:NOT-STORED"])
    # Record matching, amount limits and destination policy remain shield work.
    validate_action_params("ACCEPT_CLAIM", {
        "paypal_dispute_id": "UNKNOWN-DISPUTE", "refund_to": "attacker@example.test",
        "amount": {"minor": 999999, "currency": "USD"},
    }, [])


@pytest.mark.asyncio
async def test_one_invalid_proposal_gets_fixed_feedback_then_corrects_params():
    model = ScriptedModel([proposal(), proposal(params={"paypal_dispute_id": "PP-D-1"})])
    result = await Commander(model, NoTools()).step({"id": "incident-1"})
    assert result.params == {"paypal_dispute_id": "PP-D-1"}
    assert model.requests[1]["observations"] == [
        {"error": "INVALID_PROPOSAL_PARAMS", "action": "PROVIDE_EVIDENCE"}
    ]


@pytest.mark.asyncio
async def test_float_money_in_params_can_be_corrected_without_accepting_float_money():
    model = ScriptedModel([
        proposal("ACCEPT_CLAIM", {"paypal_dispute_id": "PP-D-1",
                                  "amount": {"minor": 60.0, "currency": "USD"}}, []),
        proposal("ACCEPT_CLAIM", {"paypal_dispute_id": "PP-D-1"}, []),
    ])
    result = await Commander(model, NoTools()).step({"id": "incident-1"})
    assert result.params == {"paypal_dispute_id": "PP-D-1"}
    assert model.requests[1]["observations"] == [
        {"error": "INVALID_PROPOSAL_PARAMS", "action": "ACCEPT_CLAIM"}
    ]


@pytest.mark.asyncio
async def test_second_invalid_proposal_aborts_with_no_fabricated_proposal():
    model = ScriptedModel([proposal(), proposal(), proposal(params={"paypal_dispute_id": "PP-D-1"})])
    with pytest.raises(CommanderError, match="^COMMANDER_PROPOSAL_INVALID$"):
        await Commander(model, NoTools()).step({"id": "incident-1"})
    assert len(model.requests) == 2


@pytest.mark.asyncio
async def test_bad_values_are_not_echoed_and_correction_budget_resets_per_step():
    bad = proposal(params={"paypal_dispute_id": "PRIVATE-UNTRUSTED-TEXT bad@example.test"})
    good = proposal(params={"paypal_dispute_id": "PP-D-1"})
    model = ScriptedModel([bad, good, bad, good])
    commander = Commander(model, NoTools())
    for _ in range(2):
        await commander.step({"id": "incident-1"})
    encoded = json.dumps(model.requests)
    assert "PRIVATE-UNTRUSTED-TEXT" not in encoded and "bad@example.test" not in encoded


@pytest.mark.asyncio
async def test_corrections_still_consume_existing_round_budget():
    model = ScriptedModel([proposal(), proposal(params={"paypal_dispute_id": "PP-D-1"})])
    with pytest.raises(CommanderError, match="^COMMANDER_BUDGET_EXHAUSTED$"):
        await Commander(model, NoTools(), max_rounds=1).step({"id": "incident-1"})
    assert len(model.requests) == 1
