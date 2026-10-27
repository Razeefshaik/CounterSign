"""Generated results must match the committed experiment and findings."""

import json
from pathlib import Path

from scripts.bundle_results import bundle, main

ROOT = Path(__file__).resolve().parents[2]


def test_generated_bundle_does_not_drift_from_sources(tmp_path):
    generated = tmp_path / "summary.json"
    assert main(["--output", str(generated)]) == 0
    assert generated.read_bytes() == (ROOT / "web/results/summary.json").read_bytes()
    data = json.loads(generated.read_text(encoding="utf-8"))
    scores = {row["system"]: row for row in data["model_off"]["scoreboard"]}
    assert scores["countersign"]["passing"] == 170
    assert scores["rules_only"]["passing"] == 215
    assert scores["countersign"]["pass_5_scenarios"] == 34
    assert scores["rules_only"]["pass_5_scenarios"] == 43
    assert all(r["error_rows"] == 0 for r in scores.values())
    assert len(data["history"]) == 6 and data["real_model"] is None
    assert {"D6", "D8", "D9", "D11", "D12"} <= {r["id"] for r in data["sandbox_findings"]}
    assert bundle() == data
