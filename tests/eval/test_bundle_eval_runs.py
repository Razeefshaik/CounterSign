"""C58 historical metrics publishing: real totals, allowlist and fail-closed privacy."""

import json
from collections import defaultdict

import pytest

from scripts.bundle_eval_runs import FIELDS, INVARIANTS, LABELS, ROOT, SOURCE, bundle, write_bundle


def fixture_root(tmp_path, change=lambda rows: rows):
    rows = [json.loads(line) for line in (ROOT / SOURCE).read_text().splitlines()]
    rows = change(rows)
    source = tmp_path / SOURCE
    source.parent.mkdir(parents=True)
    source.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return tmp_path


def test_deterministic_bytes_and_sorted_rows_independent_of_input_order(tmp_path):
    root = fixture_root(tmp_path / "input", lambda rows: list(reversed(rows)))
    output = tmp_path / "bundle.json"
    write_bundle(output, ROOT)
    first = output.read_bytes()
    write_bundle(output, root)
    assert output.read_bytes() == first
    assert b"\r" not in first and first.endswith(b"\n")
    assert (ROOT / "web/results/eval-runs.json").read_bytes() == first


def test_exact_allowlist_nested_projection_and_disclosures(tmp_path):
    def extras(rows):
        for row in rows:
            row["unapproved_extra"] = "not copied"
            row["makers_paid_on_time"]["extra"] = "not copied"
        return rows
    data = bundle(fixture_root(tmp_path, extras))
    assert set(data) == {"source", "labels", "runs"}
    assert data["source"] == SOURCE and data["labels"] == LABELS
    assert all(set(row) == set(FIELDS) for row in data["runs"])
    assert all(set(row["invariants"]) == set(INVARIANTS) for row in data["runs"])
    assert all(set(row["makers_paid_on_time"]) == {"due", "paid"} for row in data["runs"])


def test_published_totals_and_all_four_invariants():
    rows = bundle()["runs"]
    assert len(rows) == 480
    for system, passing, consistent in [("countersign", 170, 34), ("rules_only", 215, 43)]:
        selected = [r for r in rows if r["system"] == system]
        assert len(selected) == 240 and sum(r["passed"] for r in selected) == passing
        groups = defaultdict(list)
        for row in selected:
            groups[row["scenario_id"]].append(row)
        assert len(groups) == 48
        assert all({r["k"] for r in group} == set(range(5)) for group in groups.values())
        assert sum(all(r["passed"] for r in group) for group in groups.values()) == consistent
        assert all(sum(row["invariants"][key] for row in selected) == 0 for key in INVARIANTS)


@pytest.mark.parametrize("value", [
    "Bearer planted-private-value", "client_secret=planted-value", "AA" + "x" * 60,
    "eyJ" + "x" * 30 + ".payload.", "someone@private.invalid",
    "AIza" + "x" * 35, "AQ." + "x" * 40, "sk-proj-" + "x" * 25,
    "ghp_" + "x" * 30, "AKIA" + "A" * 16,
])
def test_guard_refuses_planted_values_without_replacing_output(tmp_path, value):
    def plant(rows):
        rows[0]["scenario_id"] = value
        return rows
    root = fixture_root(tmp_path / "input", plant)
    output = tmp_path / "existing.json"
    output.write_bytes(b"previous artifact\n")
    with pytest.raises(ValueError, match="PRIVATE_VALUE") as error:
        write_bundle(output, root)
    assert value not in str(error.value)
    assert output.read_bytes() == b"previous artifact\n"


def test_guard_checks_even_fields_that_would_be_dropped(tmp_path):
    def plant(rows):
        rows[0]["access_token"] = "planted-value"
        return rows
    with pytest.raises(ValueError, match="PROOF_PRIVATE_FIELD"):
        bundle(fixture_root(tmp_path, plant))


@pytest.mark.parametrize("change", [
    lambda rows: rows[:-1],
    lambda rows: [rows[0], *rows[0:-1]],
    lambda rows: [{**rows[0], "ground": "sandbox"}, *rows[1:]],
    lambda rows: [{**rows[0], "invariants": dict.fromkeys(INVARIANTS, False)}, *rows[1:]],
])
def test_incomplete_duplicate_or_relabelled_data_is_refused(tmp_path, change):
    with pytest.raises(ValueError, match="EVAL_RUN"):
        bundle(fixture_root(tmp_path, change))
