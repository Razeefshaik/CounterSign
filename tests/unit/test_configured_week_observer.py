"""Configured-week cash and payout invariants use fixed resource evidence only."""

import copy
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from scripts.configured_week import ConfiguredWeekObserver, _evidence
from scripts.slice_e2e import SliceError

NOW = datetime(2026, 11, 2, 9, tzinfo=UTC)


def attempt(identifier="prior", *, line="line", number=1, state="UNCLAIMED", amount=30000):
    return {"id": identifier, "line_id": line, "attempt_no": number, "state": state,
            "amount_minor": amount, "currency": "USD", "paypal_item_id": "ITEM-" + identifier,
            "paypal_batch_id": "BATCH-" + identifier, "sender_item_id": "SENDER-" + identifier,
            "sender_batch_id": "BATCH-SENDER-" + identifier, "payee_email": "maker@example.test",
            "operation_id": "OP-" + identifier, "prepared_at": NOW}


def item(row, status):
    return {"payout_item_id": row["paypal_item_id"], "payout_batch_id": row["paypal_batch_id"],
            "transaction_status": status, "payout_item": {
                "receiver": row["payee_email"], "sender_item_id": row["sender_item_id"],
                "amount": {"value": f"{row['amount_minor'] // 100}.{row['amount_minor'] % 100:02}",
                           "currency": "USD"}}}


def baseline():
    return {"start_available_minor": 150000, "baseline_items": {"prior": "UNCLAIMED"},
            "baseline_refunds": {}, "items": {}, "refunds": {}, "successes": {}}


def rows(attempts, *, obligations=(), allocations=(), disputes=(), journals=()):
    return {"payout_attempts": attempts, "payout_lines": [], "obligations": list(obligations),
            "payout_line_obligations": list(allocations), "disputes": list(disputes),
            "ledger_journals": list(journals)}


def observe(state, data, items, details=None, now=NOW):
    return _evidence(state, data["payout_attempts"], items, data["disputes"], details or {}, data, now)


def test_early_return_changes_cash_once_and_survives_repeated_gets():
    old = attempt()
    data = rows([old])
    state, complete = observe(baseline(), data, {"prior": item(old, "RETURNED")})
    assert complete and state["expected_available_minor"] == 180000
    resumed, complete = observe(state, data, {"prior": item(old, "RETURNED")}, now=NOW + timedelta(days=3))
    assert complete and resumed["expected_available_minor"] == 180000
    assert resumed["items"]["prior"]["returned_at"] == NOW.isoformat()


def test_new_payout_pending_then_success_is_one_outflow_not_two():
    old, new = attempt(), attempt("new", line="new-line", state="PENDING", amount=48600)
    data = rows([old, new])
    state, complete = observe(baseline(), data,
                              {"prior": item(old, "RETURNED"), "new": item(new, "PENDING")})
    assert complete and state["expected_available_minor"] == 131400
    state, _ = observe(state, data, {"prior": item(old, "RETURNED"), "new": item(new, "SUCCESS")})
    assert state["expected_available_minor"] == 131400
    assert state["successes"] == {"new-line": ["new"]}


def test_new_returned_or_failed_attempt_has_no_net_cash_outflow():
    old, new = attempt(), attempt("new", line="other")
    data = rows([old, new])
    state, _ = observe(baseline(), data,
                       {"prior": item(old, "RETURNED"), "new": item(new, "UNCLAIMED")})
    assert state["expected_available_minor"] == 150000
    state, _ = observe(state, data, {"prior": item(old, "RETURNED"), "new": item(new, "RETURNED")})
    assert state["expected_available_minor"] == 180000


def test_refund_comes_from_identity_matched_dispute_get_once():
    old = attempt()
    dispute = {"id": "d", "paypal_dispute_id": "PP-D-1", "order_id": "order", "phase": "LOST_FULL"}
    data = rows([old], disputes=[dispute])
    details = {"PP-D-1": {"dispute_id": "PP-D-1", "dispute_outcome": {
        "amount_refunded": {"value": "60.00", "currency_code": "USD"}}}}
    state, _ = observe(baseline(), data, {"prior": item(old, "RETURNED")}, details)
    assert state["expected_available_minor"] == 174000
    state, _ = observe(state, data, {"prior": item(old, "RETURNED")}, details)
    assert state["expected_available_minor"] == 174000


def test_unknown_inflight_item_is_pending_evidence_not_guessed_cash():
    old, new = attempt(), attempt("new", line="other", state="UNKNOWN")
    new["paypal_item_id"] = None
    state, complete = observe(baseline(), rows([old, new]), {"prior": item(old, "RETURNED")})
    assert complete is False and state["expected_available_minor"] == 180000


@pytest.mark.parametrize("change", ["identity", "amount", "payee"])
def test_payout_get_cannot_adopt_a_different_resource_or_amount(change):
    old = attempt()
    detail = item(old, "RETURNED")
    if change == "identity":
        detail["payout_item_id"] = "OTHER"
    elif change == "amount":
        detail["payout_item"]["amount"]["value"] = "1.00"
    else:
        detail["payout_item"]["receiver"] = "other@example.test"
    with pytest.raises(SliceError, match="^CONFIGURED_PAYOUT_IDENTITY_MISMATCH$"):
        observe(baseline(), rows([old]), {"prior": detail})


def test_two_successful_attempts_on_one_line_fail_even_after_prior_observation():
    first = attempt("new", line="paid-line", state="SUCCESS")
    state, _ = observe(baseline(), rows([first]), {"new": item(first, "SUCCESS")})
    second = attempt("second", line="paid-line", number=2, state="SUCCESS")
    with pytest.raises(SliceError, match="^CONFIGURED_LINE_PAID_TWICE$"):
        observe(state, rows([first, second]), {"new": item(first, "SUCCESS"),
                                             "second": item(second, "SUCCESS")})


def test_disputed_order_share_cannot_be_paid_but_same_makers_other_order_can():
    new = attempt("new", line="new-line", state="SUCCESS")
    dispute = {"id": "dispute", "paypal_dispute_id": "PP-D-1", "order_id": "disputed", "phase": "ACTIVE"}
    obligation = {"id": "share", "order_id": "safe-order", "state": "SETTLED"}
    allocation = {"line_id": "new-line", "obligation_id": "share", "active": True}
    data = rows([new], obligations=[obligation], allocations=[allocation], disputes=[dispute])
    details = {"PP-D-1": {"dispute_id": "PP-D-1"}}
    state, complete = observe(baseline(), data, {"new": item(new, "SUCCESS")}, details)
    assert complete and state["expected_available_minor"] == 120000
    obligation["order_id"] = "disputed"
    with pytest.raises(SliceError, match="^CONFIGURED_DISPUTED_SHARE_PAID$"):
        observe(baseline(), data, {"new": item(new, "SUCCESS")}, details)


def test_replacement_needs_return_get_before_prepared_time():
    old, replacement = attempt(), attempt("replacement", number=2, state="SUCCESS")
    data = rows([old, replacement])
    with pytest.raises(SliceError, match="^CONFIGURED_REPLACEMENT_BEFORE_RETURN$"):
        observe(baseline(), data,
                {"prior": item(old, "UNCLAIMED"), "replacement": item(replacement, "SUCCESS")})
    journal = {"operation_id": old["operation_id"], "memo": "PAYOUT_RETURNED: confirmed",
               "occurred_at": NOW - timedelta(seconds=1)}
    data["ledger_journals"] = [journal]
    state, complete = observe(baseline(), data,
        {"prior": item(old, "RETURNED"), "replacement": item(replacement, "SUCCESS")},
        now=NOW + timedelta(seconds=1))
    assert complete and state["expected_available_minor"] == 150000


class Store:
    def __init__(self, directory):
        self.directory = directory
        self.data = {"prior_item_id": "ITEM-prior"}
        self.clock = lambda: NOW

    def checkpoint(self, **updates):
        self.data.update(copy.deepcopy(updates))

    def record(self, event, **details):
        assert event == "CONFIGURED_MODEL_CASH"


def snapshot(amount):
    return {"cash": {"paypal": {"available": {"minor": amount, "currency": "USD"}}}}


@pytest.mark.asyncio
async def test_observer_adopts_old_checkpoint_only_with_recorded_initial_cash_and_waits_for_lag(tmp_path):
    old = attempt()
    data = rows([old])
    store = Store(tmp_path)
    observer = ConfiguredWeekObserver(None, None, store)

    async def load():
        return data

    observer._rows = load
    with pytest.raises(SliceError, match="^CONFIGURED_INITIAL_CASH_UNCONFIRMED$"):
        await observer.start(snapshot(180000))
    (tmp_path / "run.jsonl").write_text(json.dumps({"event": "MONDAY_DISPUTE_FROZEN",
                                                   "available_minor": 150000}) + "\n", encoding="utf-8")
    await observer.start(snapshot(180000))

    class HTTP:
        async def request(self, method, path):
            assert method == "GET" and path == "/v1/payments/payouts-item/ITEM-prior"
            return SimpleNamespace(status_code=200, body=item(old, "RETURNED"))

    observer.http = HTTP()
    result = await observer.observe(snapshot(150000))
    assert result == {"cash_matches": False, "expected_available_minor": 180000,
                      "actual_available_minor": 150000, "resources_confirmed": True}
    assert (await observer.observe(snapshot(180000)))["cash_matches"] is True
    saved = copy.deepcopy(store.data)
    await observer.start(snapshot(35400))
    assert saved == store.data


def test_unresolved_dispute_null_outcome_is_not_a_refund():
    data = rows([], disputes=[{"id": "d", "paypal_dispute_id": "PP-D-1", "order_id": "order",
                              "phase": "ACTIVE"}])
    state, complete = observe(baseline(), data, {}, {"PP-D-1": {
        "dispute_id": "PP-D-1", "dispute_outcome": None}})
    assert complete and state["expected_available_minor"] == 150000
