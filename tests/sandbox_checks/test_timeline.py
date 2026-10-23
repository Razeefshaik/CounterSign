import json
from datetime import UTC, datetime

import pytest

from scripts.sandbox_checks import timeline
from scripts.sandbox_checks.recorder import Recorder
from scripts.sandbox_checks.redact import Redactor
from scripts.sandbox_checks.runner import CheckContext

NOW = datetime(2026, 10, 24, 9, 0, tzinfo=UTC)


def context(root, check_id):
    state = {"schema_version": 1, "check_id": check_id, "completed_steps": [], "data": {}}
    recorder = Recorder(check_id, redactor=Redactor(), root=root)
    return CheckContext(
        client=None,
        recorder=recorder,
        manual=None,
        state=state,
        state_path=root / "2026-10-24" / check_id / "state.json",
        clock=lambda: NOW,
        output=lambda _: None,
    )


def test_record_checkpoints_once_and_load_all_merges_checks_in_time_order(tmp_path):
    d3 = context(tmp_path, "D3")
    first = timeline.record(
        d3, "PAYOUT_SUBMITTED", resource_id="BATCH1", amount_minor=100, currency="USD"
    )
    again = timeline.record(d3, "PAYOUT_SUBMITTED", resource_id="BATCH1", amount_minor=999)
    assert again == first
    saved = json.loads(d3.state_path.read_text(encoding="utf-8"))
    assert saved["data"]["timeline"] == [first]
    assert first == {
        "at": "2026-10-24T09:00:00Z",
        "source": "D3",
        "event": "PAYOUT_SUBMITTED",
        "resource_id": "BATCH1",
        "amount_minor": 100,
        "currency": "USD",
        "paypal_time": None,
    }
    d2 = context(tmp_path, "D2")
    timeline.record(d2, "CAPTURE_COMPLETED", resource_id="CAP1", at="2026-10-24T08:00:00Z")
    assert [entry["resource_id"] for entry in timeline.load_all(tmp_path)] == ["CAP1", "BATCH1"]


def test_unknown_events_are_rejected(tmp_path):
    with pytest.raises(ValueError, match="Unknown timeline event"):
        timeline.record(context(tmp_path, "D2"), "MONEY_MOVED", resource_id="X")


def test_every_event_names_webhooks_and_a_valid_balance_effect():
    for webhooks, effect in timeline.EVENTS.values():
        assert webhooks
        assert effect is None or (effect[0] in ("available", "withheld") and effect[1] in (1, -1))
