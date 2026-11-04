"""C65: deterministic route coverage from committed, redacted exchanges only."""

import argparse
import json
import re
from collections import Counter
from pathlib import Path

from mock_paypal.call_routes import classify

ROOT = Path(__file__).resolve().parents[1]
SOURCE = "Recorded: real PayPal sandbox"


def build(root=ROOT):
    index = (root / "recordings/sandbox/INDEX.md").read_text(encoding="utf-8")
    expected = {}
    for line in index.splitlines():
        if re.match(r"\| D\d+ \|", line):
            cells = [s.strip() for s in line.strip("|").split("|")]
            expected[cells[0]] = sum(int(n) for n in re.findall(r"\d{4}-\d{2}-\d{2}: (\d+)", cells[3]))
    groups, counts = {}, Counter()
    for check in sorted(expected, key=lambda s:int(s[1:])):
        for path in sorted((root / "recordings/sandbox").glob(f"????-??-??/{check}/*.json")):
            if not re.match(r"\d+_", path.name):
                continue
            record = json.loads(path.read_text(encoding="utf-8"))
            if "method" not in record or "path" not in record:
                continue
            counts[check] += 1
            product, route = classify(record["path"])
            method = record["method"]
            if product == "Other" or method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                raise ValueError("C65_UNKNOWN_RECORDED_ROUTE")
            key = (product, method, route)
            row = groups.setdefault(key, {"product":product, "method":method, "route":route,
                "source":SOURCE, "attempts":0, "status_codes":set(), "transport_errors":0,
                "check_ids":set(), "recordings":[]})
            status = record.get("response_status")
            row["attempts"] += 1
            if type(status) is int:
                row["status_codes"].add(status)
            else:
                row["transport_errors"] += 1
            row["check_ids"].add(check)
            row["recordings"].append({"check_id":check, "path":path.relative_to(root).as_posix(), "status":status})
    if dict(counts) != expected or sum(counts.values()) != 1041 or sum(expected.values()) != 1041:
        raise ValueError("C65_COVERAGE_COUNT_MISMATCH")
    rows = []
    for number, key in enumerate(sorted(groups), 1):
        row = groups[key]
        rows.append({**row, "id":number, "status_codes":sorted(row["status_codes"]),
                     "check_ids":sorted(row["check_ids"], key=lambda s:int(s[1:]))})
    return {"source":SOURCE, "index":"recordings/sandbox/INDEX.md", "total_attempts":sum(counts.values()), "rows":rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    output = ROOT / "web/results/paypal-coverage.json"
    encoded = json.dumps(build(), indent=2, ensure_ascii=False) + "\n"
    if args.check:
        if not output.exists() or output.read_text(encoding="utf-8") != encoded:
            raise SystemExit("C65_COVERAGE_OUTDATED")
    else:
        output.write_text(encoded, encoding="utf-8")
    print("C65 coverage: 1041 recorded attempts")


if __name__ == "__main__":
    main()
