import re
from pathlib import Path

import pytest

from countersign.fsm import dispute
from countersign.fsm.dispute import KEEPS_RESERVE, OPENS_INCIDENT, OUTCOME_MAP, Phase, phase

DOCS = Path(__file__).resolve().parents[3] / "docs"

# Synthetic codes: the real ones arrive only from the D10 recordings.
TEST_MAP = {
    "TEST_WIN": Phase.WON,
    "TEST_LOSS": Phase.LOST_FULL,
    "TEST_PARTIAL": Phase.LOST_PARTIAL,
    "TEST_CANCEL": Phase.CANCELLED,
    "TEST_ACTIVE": Phase.ACTIVE,
    "TEST_UNSURE": Phase.UNKNOWN_OUTCOME,
}
AMOUNT = 40000  # $400.00 disputed


def resolved(code, refunded, amount=AMOUNT):
    return phase("RESOLVED", code, refunded, amount, outcome_map=TEST_MAP)


def contracts_enum(label):
    text = (DOCS / "CONTRACTS.md").read_text(encoding="utf-8")
    line = next(line for line in text.splitlines() if f"**{label}:**" in line)
    return re.findall(r"`([A-Z_]+)`", line.split(f"**{label}:**", 1)[1].split("**", 1)[0])


def plan_check(table, column):
    plan = (DOCS / "PLAN.md").read_text(encoding="utf-8")
    block = plan.split(f"CREATE TABLE {table} (", 1)[1].split("CREATE TABLE", 1)[0]
    found = re.search(rf"{column} text (?:NOT NULL )?CHECK \({column} IN \(([^)]*)\)", block)
    return re.findall(r"'([A-Z_]+)'", found[1])


def test_phases_match_the_plan_check_list_and_the_dashboard_contract():
    assert [p.value for p in Phase] == plan_check("disputes", "phase")
    assert [p.value for p in Phase] == contracts_enum("Dispute phases")


@pytest.mark.parametrize(
    "status",
    [
        "OPEN",
        "WAITING_FOR_SELLER_RESPONSE",
        "WAITING_FOR_BUYER_RESPONSE",
        "UNDER_REVIEW",
        "OTHER",
        "resolved",
        None,
    ],
)
def test_any_status_but_resolved_is_active_whatever_the_outcome_fields(status):
    assert phase(status, "TEST_WIN", 0, AMOUNT, outcome_map=TEST_MAP) is Phase.ACTIVE
    assert phase(status, "TEST_LOSS", AMOUNT, AMOUNT, outcome_map=TEST_MAP) is Phase.ACTIVE


def test_outcome_map_contains_only_d10_confirmed_codes():
    assert OUTCOME_MAP == {
        "RESOLVED_SELLER_FAVOUR": Phase.WON,
        "RESOLVED_BUYER_FAVOUR": Phase.LOST_FULL,
    }


@pytest.mark.parametrize("code", ["CANCELED_BY_BUYER", "RESOLVED_SELLER_FAVOR", "NEW_CODE"])
def test_unconfirmed_codes_still_fail_closed(code):
    assert phase("RESOLVED", code, 0, AMOUNT) is Phase.UNKNOWN_OUTCOME


def test_d10_seller_win_releases_only_without_a_contradictory_refund():
    assert phase("RESOLVED", "RESOLVED_SELLER_FAVOUR", 0, AMOUNT) is Phase.WON
    assert phase("RESOLVED", "RESOLVED_SELLER_FAVOUR", None, AMOUNT) is Phase.WON
    assert phase("RESOLVED", "RESOLVED_SELLER_FAVOUR", 1, AMOUNT) is Phase.UNKNOWN_OUTCOME


@pytest.mark.parametrize(
    ("refunded", "expected"),
    [
        (AMOUNT, Phase.LOST_FULL),
        (15000, Phase.LOST_PARTIAL),
        (0, Phase.UNKNOWN_OUTCOME),
        (None, Phase.UNKNOWN_OUTCOME),
        (AMOUNT + 1, Phase.UNKNOWN_OUTCOME),
    ],
)
def test_d10_buyer_win_sizes_loss_from_confirmed_refund(refunded, expected):
    assert phase("RESOLVED", "RESOLVED_BUYER_FAVOUR", refunded, AMOUNT) is expected


@pytest.mark.parametrize("code", [None, "", "NOT_IN_MAP", "test_win"])
def test_missing_or_unmapped_codes_fail_closed(code):
    assert resolved(code, 0) is Phase.UNKNOWN_OUTCOME


@pytest.mark.parametrize(
    ("code", "expected"), [("TEST_WIN", Phase.WON), ("TEST_CANCEL", Phase.CANCELLED)]
)
@pytest.mark.parametrize("refunded", [None, 0])
def test_win_and_cancel_with_nothing_refunded(code, expected, refunded):
    assert resolved(code, refunded) is expected


@pytest.mark.parametrize("code", ["TEST_WIN", "TEST_CANCEL"])
@pytest.mark.parametrize("refunded", [1, AMOUNT, -1])
def test_win_or_cancel_with_money_refunded_is_contradictory(code, refunded):
    assert resolved(code, refunded) is Phase.UNKNOWN_OUTCOME


@pytest.mark.parametrize("code", ["TEST_LOSS", "TEST_PARTIAL"])
def test_the_refunded_amount_decides_full_or_partial_loss(code):
    assert resolved(code, AMOUNT) is Phase.LOST_FULL
    assert resolved(code, 15000) is Phase.LOST_PARTIAL
    assert resolved(code, 1) is Phase.LOST_PARTIAL
    assert resolved(code, AMOUNT - 1) is Phase.LOST_PARTIAL


@pytest.mark.parametrize("code", ["TEST_LOSS", "TEST_PARTIAL"])
@pytest.mark.parametrize(
    ("refunded", "amount"),
    [
        (None, AMOUNT),
        (AMOUNT, None),
        (0, AMOUNT),
        (-1, AMOUNT),
        (AMOUNT + 1, AMOUNT),
        (0, 0),
        (5, -5),
    ],
)
def test_a_loss_that_the_amounts_cannot_size_fails_closed(code, refunded, amount):
    assert resolved(code, refunded, amount) is Phase.UNKNOWN_OUTCOME


def test_a_code_mapped_to_active_or_unknown_still_fails_closed():
    assert resolved("TEST_ACTIVE", 0) is Phase.UNKNOWN_OUTCOME
    assert resolved("TEST_UNSURE", 0) is Phase.UNKNOWN_OUTCOME


def test_unknown_outcome_keeps_the_reserve_and_opens_an_incident():
    assert Phase.UNKNOWN_OUTCOME in KEEPS_RESERVE
    assert Phase.UNKNOWN_OUTCOME in OPENS_INCIDENT
    assert KEEPS_RESERVE == {Phase.ACTIVE, Phase.UNKNOWN_OUTCOME}
    assert OPENS_INCIDENT == {Phase.UNKNOWN_OUTCOME}


def test_filling_the_shared_map_is_all_it_takes(monkeypatch):
    monkeypatch.setitem(dispute.OUTCOME_MAP, "TEST_WIN", Phase.WON)
    assert phase("RESOLVED", "TEST_WIN", None, AMOUNT) is Phase.WON


def test_an_injected_map_overrides_the_shared_one_and_always_yields_a_phase():
    result = phase("RESOLVED", "X", 0, AMOUNT, outcome_map={"X": "CANCELLED"})
    assert type(result) is Phase and result is Phase.CANCELLED
