"""C50: reproducible source-derived public evidence and fail-closed privacy."""

import json
from pathlib import Path

import pytest

from scripts import build_sandbox_proof as proof


def facts(value):
    if isinstance(value, dict):
        if "value" in value:
            yield value
        for item in value.values():
            yield from facts(item)
    elif isinstance(value, list):
        for item in value:
            yield from facts(item)


def test_committed_bundle_is_byte_identical_and_covers_all_checks():
    first = proof.encoded()
    assert first == proof.encoded() == (proof.ROOT / proof.OUTPUT).read_bytes()
    result = json.loads(first)
    assert [check["id"] for check in result["checks"]] == [f"D{n}" for n in range(1, 14)]
    summary = {key: value["value"] for key, value in result["summary"].items()}
    assert summary == {
        "checks_run": 13,
        "http_exchanges": 1041,
        "http_responses": 1007,
        "transport_errors": 34,
        "signatures_verified": 47,
        "payouts_succeeded": 6,
        "disputes_settled": 3,
    }


def test_every_fact_has_existing_relative_source_and_recorded_value():
    result = proof.build()
    checked = 0
    for item in facts(result):
        sources = item.get("sources", [item.get("source")])
        assert sources and all(isinstance(s, str) for s in sources)
        for source in sources:
            path = proof.ROOT / source
            assert (
                path.is_file() and not Path(source).is_absolute() and ".." not in Path(source).parts
            )
        pointer = item.get("pointer", "")
        if pointer.startswith(("/response_body/", "/response_headers/")):
            row = json.loads((proof.ROOT / item["source"]).read_text(encoding="utf-8"))
            for key in pointer.strip("/").split("/"):
                key = key.replace("~1", "/").replace("~0", "~")
                row = row[int(key)] if isinstance(row, list) else row[key]
            assert row == item["value"]
            checked += 1
    assert checked > 100


def test_summary_deduplicates_successes_and_keeps_simulator_and_mock_separate():
    result = proof.build()
    [d11] = [c for c in result["checks"] if c["id"] == "D11"]
    assert d11["verification_counts"]["value"] == {"SUCCESS": 47}
    simulator = "recordings/sandbox/2026-10-26/D11/07_verify_signature.json"
    assert simulator in result["summary"]["signatures_verified"]["sources"]
    assert "simulator" in result["summary"]["signatures_verified"]["definition"]
    payout_sources = result["summary"]["payouts_succeeded"]["sources"]
    assert len(payout_sources) == 6 and all("/D7/" not in s for s in payout_sources)
    assert result["real_model"]["model"]["value"] == "gemini-3.5-flash-lite"
    assert result["real_model"]["status"]["value"] == "COMPLETE_WITH_GAPS"
    assert result["real_model"]["ground"]["value"] == "local mock, not the sandbox"
    waits = [
        r
        for r in result["real_model"]["decisions"]
        if r.get("observed_action", {}).get("value") == "WAIT"
    ]
    assert len(waits) == 2 and all("shield_outcome" not in r for r in waits)


@pytest.mark.parametrize(
    "planted",
    [
        "Bearer planted-private-token-123456",
        "Basic cHJpdmF0ZTpwYXNzd29yZA==",
        "client_id=planted-client",
        "client_secret=planted-secret",
        "AA" + "x" * 78,
        "private@merchant.test",
        "alias@example.com.evil",
        {"access_token": "planted"},
        {"client_secret": "planted"},
    ],
)
def test_secret_guard_refuses_before_overwriting_output(tmp_path, monkeypatch, planted):
    destination = tmp_path / "proof.json"
    destination.write_bytes(b"existing safe evidence")
    monkeypatch.setattr(proof, "model_run", lambda root: {"planted": planted})
    with pytest.raises(ValueError, match="^PROOF_PRIVATE_(FIELD|VALUE)$"):
        proof.write(output=destination)
    assert destination.read_bytes() == b"existing safe evidence"


def test_role_aliases_are_permitted_but_never_required():
    proof.public_guard(["maker-a@example.test", "buyer@example.com", "role@example"])
