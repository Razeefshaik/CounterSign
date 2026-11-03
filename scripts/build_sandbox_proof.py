"""C50: deterministic public proof from committed, redacted evidence only.

No environment files, database, PayPal or model access. Facts carry their source
file (and a JSON pointer/line where useful); aggregate counts list contributing
recordings. Only explicitly selected public fields can enter the bundle.
"""

import argparse
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FINDINGS = "docs/SANDBOX_FINDINGS.md"
INDEX = "recordings/sandbox/INDEX.md"
MODEL = "recordings/real_model/2026-10-27"
OUTPUT = "web/results/sandbox-proof.json"
EMAIL = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@([A-Za-z0-9.-]+)")
PRIVATE = re.compile(
    r"\b(?:bearer|basic)\s+[A-Za-z0-9+/_.=-]{8,}|"
    r"\b(?:client[_ -]?(?:id|secret)|access[_ -]?token|refresh[_ -]?token|api[_ -]?key|password)"
    r"[\"']?\s*[:=]\s*[\"']?(?!\[?REDACTED)[^\s\"',;}]+|"
    r"\bA[AB][A-Za-z0-9_-]{50,}\b|\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]+\.",
    re.IGNORECASE,
)


def public_guard(value):
    """Fail closed before writing; never include the planted value in errors."""
    if isinstance(value, dict):
        for key, item in value.items():
            if re.search(
                r"(?:client_?(?:id|secret)|access_?token|refresh_?token|password|api_?key)",
                key,
                re.IGNORECASE,
            ):
                raise ValueError("PROOF_PRIVATE_FIELD")
            public_guard(key)
            public_guard(item)
    elif isinstance(value, list):
        for item in value:
            public_guard(item)
    elif isinstance(value, str):  # noqa: SIM102 - keep the string-only branch explicit.
        if PRIVATE.search(value) or any(
            not re.fullmatch(r"example(?:\.(?:com|org|test))?", match[1], re.IGNORECASE)
            for match in EMAIL.finditer(value)
        ):
            raise ValueError("PROOF_PRIVATE_VALUE")


def fact(value, source, pointer=None):
    result = {"value": value, "source": source}
    if pointer is not None:
        result["pointer"] = pointer
    return result


def aggregate(value, sources, definition):
    return {"value": value, "sources": sorted(set(sources)), "definition": definition}


def table(path):
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.startswith("| D"):
            yield number, [cell.strip().replace("**", "") for cell in line.strip("|").split("|")]


def nodes(value, pointer="/response_body"):
    if isinstance(value, dict):
        yield pointer, value
        for key, item in sorted(value.items()):
            yield from nodes(item, pointer + "/" + key.replace("~", "~0").replace("/", "~1"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from nodes(item, pointer + "/" + str(index))


def resources(record, source):
    """Extract only explicit PayPal resource IDs/statuses, never raw responses."""
    path = record.get("path", "")
    if not path.startswith(
        (
            "/v1/payments/payouts",
            "/v1/customer/disputes",
            "/v2/checkout/orders",
            "/v2/payments/captures",
        )
    ):
        return []
    found = []
    for pointer, row in nodes(record.get("response_body")):
        identities = [
            ("payout_item", "payout_item_id", "transaction_status"),
            ("payout_batch", "payout_batch_id", "batch_status"),
            ("dispute", "dispute_id", "status"),
        ]
        if "/captures/" in pointer or (
            pointer == "/response_body" and path.startswith("/v2/payments/captures/")
        ):
            identities.append(("capture", "id", "status"))
        for kind, id_key, status_key in identities:
            identifier, status = row.get(id_key), row.get(status_key)
            if not isinstance(identifier, str) or not re.fullmatch(r"[A-Z0-9-]{5,64}", identifier):
                continue
            if not isinstance(status, str):
                continue
            item = {
                "kind": kind,
                "id": fact(identifier, source, pointer + "/" + id_key),
                "status": fact(status, source, pointer + "/" + status_key),
            }
            outcome = row.get("dispute_outcome", {}).get("outcome_code")
            if kind == "dispute" and isinstance(outcome, str):
                item["outcome"] = fact(outcome, source, pointer + "/dispute_outcome/outcome_code")
            found.append(item)
    return found


def model_run(root):
    source = MODEL + "/run.jsonl"
    events = [
        (number, json.loads(line))
        for number, line in enumerate((root / source).read_text(encoding="utf-8").splitlines(), 1)
        if line.strip()
    ]
    model_number, model = next((n, r) for n, r in events if r["event"] == "DEMO_MODEL")
    summary_number, summary = next(
        (n, r) for n, r in events if r["event"] == "CONFIGURED_MODEL_SUMMARY"
    )
    readme = MODEL + "/README.md"
    decisions = []
    for number, line in enumerate((root / readme).read_text(encoding="utf-8").splitlines(), 1):
        if not line.startswith("| ") or line.startswith("| Real model"):
            continue
        title, outcome = [cell.strip().replace("**", "") for cell in line.strip("|").split("|")]
        row = {
            "decision": fact(title, readme, f"line:{number}"),
            "outcome": fact(outcome, readme, f"line:{number}"),
        }
        code = re.match(r"([A-Z_]{2,})(?:\s|$)", title)
        if code:
            row["action"] = fact(code[1], readme, f"line:{number}")
            matching = [
                (n, r)
                for n, r in events
                if r.get("action") == code[1]
                and r["event"] in {"AGENT_DECISION", "CONFIGURED_MODEL_ACTED_EARLIER"}
            ]
        else:
            step = "injection" if title.startswith("Injection") else "settled_retry"
            matching = (
                [
                    (n, r)
                    for n, r in events
                    if r["event"] == "CONFIGURED_MODEL_DECISION_DIFFERS" and r.get("step") == step
                ]
                if not title.startswith("Friday")
                else []
            )
        for n, event in matching:
            row["observed_action"] = fact(event["action"], source, f"line:{n}/action")
            if "verdict" in event:
                row["shield_outcome"] = fact(event["verdict"], source, f"line:{n}/verdict")
            if "proposal_id" in event:
                row["proposal_id"] = fact(event["proposal_id"], source, f"line:{n}/proposal_id")
        if "shield_outcome" not in row and (verdict := re.match(r"(ALLOW|NEEDS_OWNER)\b", outcome)):
            row["shield_outcome"] = fact(verdict[1], readme, f"line:{number}")
        decisions.append(row)
    return {
        "model": fact(model["model"], source, f"line:{model_number}/model"),
        "ground": fact("local mock, not the sandbox", readme, "line:4"),
        "status": fact(summary["status"], source, f"line:{summary_number}/status"),
        "decisions": decisions,
    }


def build(root=ROOT):
    summaries = {cells[0]: (n, cells) for n, cells in table(root / FINDINGS) if len(cells) == 3}
    details = {cells[0]: (n, cells) for n, cells in table(root / FINDINGS) if len(cells) == 6}
    topics = {cells[0]: (n, cells) for n, cells in table(root / INDEX)}
    checks, all_exchanges, successful_items, settled, signatures = [], [], {}, {}, []
    responses, transport_errors = [], []
    for number in range(1, 14):
        check_id = f"D{number}"
        summary_line, summary = summaries[check_id]
        detail_line, detail = details[check_id]
        topic_line, topic = topics[check_id]
        exchanges = []
        for path in sorted((root / "recordings/sandbox").glob(f"????-??-??/{check_id}/*.json")):
            if not re.match(r"\d+_", path.name):
                continue
            record = json.loads(path.read_text(encoding="utf-8"))
            if "method" in record and "path" in record:
                exchanges.append((path.relative_to(root).as_posix(), record))
        source_paths = [p for p, _ in exchanges]
        all_exchanges.extend(source_paths)
        observations, debug_ids, dates, verified = {}, [], [], []
        for source, record in exchanges:
            (
                responses if isinstance(record.get("response_status"), int) else transport_errors
            ).append(source)
            timestamp = record.get("t_recv") or record.get("t_sent")
            if timestamp:
                dates.append((timestamp, source))
            debug = record.get("response_headers", {}).get("paypal-debug-id")
            if debug and len(debug_ids) < 3 and debug not in [f["value"] for f in debug_ids]:
                debug_ids.append(fact(debug, source, "/response_headers/paypal-debug-id"))
            if record.get("response_status") is not None and 200 <= record["response_status"] < 300:
                for item in resources(record, source):
                    key = (item["kind"], item["id"]["value"])
                    # Observations are sorted chronologically, independently of file/date ordering.
                    if key not in observations or timestamp >= observations[key][0]:
                        observations[key] = (timestamp, item)
                    if (
                        check_id != "D7"
                        and item["kind"] == "payout_item"
                        and item["status"]["value"] == "SUCCESS"
                    ):
                        successful_items[item["id"]["value"]] = source
                    if check_id != "D7" and item.get("outcome"):
                        settled[item["id"]["value"]] = source
            if check_id == "D11" and "verify_signature" in source:
                status = (record.get("response_body") or {}).get("verification_status")
                if isinstance(status, str):
                    verified.append(fact(status, source, "/response_body/verification_status"))
                    if status == "SUCCESS":
                        signatures.append(source)
        check = {
            "id": check_id,
            "topic": fact(topic[1], INDEX, f"line:{topic_line}"),
            "verdict": fact(summary[1], FINDINGS, f"line:{summary_line}"),
            "finding": fact(summary[2], FINDINGS, f"line:{summary_line}"),
            "design_impact": fact(detail[5], FINDINGS, f"line:{detail_line}"),
            "exchanges": aggregate(
                len(exchanges),
                source_paths,
                "Numbered HTTP exchange files; includes recorded lost responses",
            ),
            "debug_ids": debug_ids,
            "resources": [
                item
                for _, item in sorted(
                    observations.values(),
                    key=lambda pair: (pair[1]["kind"], pair[1]["id"]["value"]),
                )
            ],
        }
        if dates:
            first, last = min(dates), max(dates)
            check["date_start"] = fact(first[0], first[1], "/t_recv or /t_sent")
            check["date_end"] = fact(last[0], last[1], "/t_recv or /t_sent")
        if check_id == "D11":
            check["verification_results"] = verified
            check["verification_counts"] = aggregate(
                dict(sorted(Counter(f["value"] for f in verified).items())),
                [f["source"] for f in verified],
                "Signature API responses, including the explicit simulator event",
            )
        checks.append(check)
    result = {
        "schema_version": 1,
        "checks": checks,
        "real_model": model_run(root),
        "summary": {
            "checks_run": aggregate(len(checks), [FINDINGS, INDEX], "D1 through D13"),
            "http_exchanges": aggregate(
                len(all_exchanges),
                all_exchanges,
                "Numbered D1-D13 exchange attempts, including transport errors; excludes separate balance samples and webhook files",
            ),
            "http_responses": aggregate(
                len(responses), responses, "Exchange attempts with a recorded HTTP response status"
            ),
            "transport_errors": aggregate(
                len(transport_errors),
                transport_errors,
                "Exchange attempts without a recorded HTTP response status",
            ),
            "signatures_verified": aggregate(
                len(signatures),
                signatures,
                "SUCCESS verification calls; includes one simulator event, not 47 real deliveries",
            ),
            "payouts_succeeded": aggregate(
                len(successful_items),
                successful_items.values(),
                "Distinct payout items observed SUCCESS; excludes D7 negative-test fixtures",
            ),
            "disputes_settled": aggregate(
                len(settled),
                settled.values(),
                "Distinct disputes with recorded outcome codes; includes buyer cancellation",
            ),
        },
    }
    public_guard(result)
    return result


def encoded(root=ROOT):
    return (json.dumps(build(root), ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )


def write(root=ROOT, output=None):
    data = encoded(root)  # Validate everything before opening/replacing the destination.
    destination = output or root / OUTPUT
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="Verify the committed bundle is current"
    )
    args = parser.parse_args()
    if args.check:
        if (ROOT / OUTPUT).read_bytes() != encoded():
            raise SystemExit("PROOF_BUNDLE_STALE")
    else:
        write()
    print("C50 proof bundle verified" if args.check else "C50 proof bundle written")


if __name__ == "__main__":
    main()
