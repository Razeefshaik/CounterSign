"""C65: committed attempts reproduce the index; counts are never adjusted."""

import json
import shutil
from collections import Counter

import pytest

from scripts.build_paypal_coverage import ROOT, build
from scripts.build_sandbox_proof import public_guard


def test_committed_coverage_is_deterministic_and_matches_every_index_attempt():
    value = build()
    assert value == json.loads((ROOT / "web/results/paypal-coverage.json").read_text(encoding="utf-8"))
    assert value["total_attempts"] == sum(row["attempts"] for row in value["rows"]) == 1041
    assert sum(row["transport_errors"] for row in value["rows"]) == 34
    checks = Counter(item["check_id"] for row in value["rows"] for item in row["recordings"])
    assert set(checks) == {"D"+str(n) for n in range(1,14)}
    assert len({p["path"] for row in value["rows"] for p in row["recordings"]}) == 1041
    public_guard(value)


def test_missing_attempt_refuses_instead_of_revising_expected_total(tmp_path):
    destination = tmp_path / "recordings/sandbox"
    destination.mkdir(parents=True)
    shutil.copyfile(ROOT / "recordings/sandbox/INDEX.md", destination / "INDEX.md")
    with pytest.raises(ValueError, match="C65_COVERAGE_COUNT_MISMATCH"):
        build(tmp_path)
