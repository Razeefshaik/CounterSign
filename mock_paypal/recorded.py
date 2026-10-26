"""Published, redacted D7 response fixtures; no runtime sandbox or credential access."""

import json
from copy import deepcopy
from functools import cache
from pathlib import Path
from urllib.parse import urlsplit


@cache
def negative_fixtures():
    return json.loads(
        Path(__file__)
        .with_name("fixtures")
        .joinpath("d7_responses.json")
        .read_text(encoding="utf-8")
    )


def negative_response(value, method, path, base, debug):
    entries = [
        entry
        for entry in negative_fixtures()
        if entry["value"] == value and entry["method"] == method
    ]
    match = next((entry for entry in entries if entry["path"] == path), None)
    if match is None and len(entries) == 1:
        match = entries[0]
    if match is None:
        return None

    def local(value):
        if isinstance(value, list):
            return [local(item) for item in value]
        if isinstance(value, dict):
            result = {key: local(item) for key, item in value.items()}
            if "debug_id" in result:
                result["debug_id"] = debug
            href = result.get("href")
            if isinstance(href, str):
                parsed = urlsplit(href)
                if parsed.hostname in {"api.sandbox.paypal.com", "api-m.sandbox.paypal.com"}:
                    result["href"] = base.rstrip("/") + parsed.path
            return result
        return value

    return match["status"], local(deepcopy(match["body"]))
