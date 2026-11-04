"""Persistent paid-request ceiling; public metadata only, never request contents."""

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from countersign.agent.models import ModelError

INPUT_PRICE = Decimal("0.0000015")
OUTPUT_PRICE = Decimal("0.000009")
OUTPUT_LIMIT = 4096


class Budget:
    """Reserve worst-case token cost before sending, retain failures and stops.

    UTF-8 byte count plus overhead conservatively bounds text input tokens.
    An explicit output cap includes reasoning. Missing/error usage keeps the
    full reservation charged to this local ceiling; never assume it was free.
    This is an estimate, not an OpenRouter billing statement.
    """

    def __init__(self, path, *, dollars, requests, model="google/gemini-3.5-flash"):
        self.path = Path(path)
        self.limit, self.request_limit = Decimal(str(dollars)), requests
        if model not in {"google/gemini-3.5-flash", "google/gemma-4-31b-it:free"}:
            raise ValueError("C67_MODEL_REFUSED")
        self.free = model.endswith(":free")
        self.state = {"version": 1, "model": model,
            "dollar_limit": str(self.limit), "request_limit": requests,
            "requests": [], "stops": [], "output_token_limit": OUTPUT_LIMIT}
        if self.path.exists():
            self.state = json.loads(self.path.read_text(encoding="utf-8"))
            if (self.state["dollar_limit"] != str(self.limit)
                    or self.state["request_limit"] != requests
                    or self.state["model"] != model):
                raise ValueError("C67_BUDGET_RESUME_MISMATCH")
        self.pending = None
        self.save()

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.state, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.path)

    @property
    def charged(self):
        return sum((Decimal(row["ceiling_charge_usd"]) for row in self.state["requests"]),
                   Decimal(0))

    def before_request(self, payload):
        if payload.get("model") != self.state["model"]:
            raise ModelError("MODEL_ID_MISMATCH")
        payload["max_tokens"] = OUTPUT_LIMIT
        estimate = (Decimal(0) if self.free else
                    Decimal(len(json.dumps(payload).encode()) + 4096) * INPUT_PRICE
                    + OUTPUT_LIMIT * OUTPUT_PRICE)
        if (len(self.state["requests"]) >= self.request_limit
                or self.charged + estimate > self.limit):
            self.state["stops"].append({"at": datetime.now(UTC).isoformat(),
                                       "reason": "C67_BUDGET_EXHAUSTED"})
            self.save()
            raise ModelError("C67_BUDGET_EXHAUSTED")
        self.pending = {"at": datetime.now(UTC).isoformat(), "status": "pending_or_transport_error",
            "ceiling_charge_usd": str(estimate), "reserved_usd": str(estimate)}
        self.state["requests"].append(self.pending)
        self.save()

    def after_response(self, response):
        if self.pending is None:
            raise ModelError("C67_BUDGET_STATE_INVALID")
        row, self.pending = self.pending, None
        row["status"] = response.status_code
        if response.status_code == 200:
            body = response.json()
            row["reported_model"] = body.get("model")
            usage = body.get("usage", {})
            for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                value = usage.get(key)
                if type(value) is int and value >= 0:
                    row[key] = value
            if all(key in row for key in ("prompt_tokens", "completion_tokens")):
                cost = (Decimal(0) if self.free else row["prompt_tokens"] * INPUT_PRICE
                        + row["completion_tokens"] * OUTPUT_PRICE)
                row["ceiling_charge_usd"] = str(cost)
            actual = usage.get("cost")
            if type(actual) in (int, float, str):
                try:
                    cost = Decimal(str(actual))
                    if cost.is_finite() and cost >= 0:
                        row["reported_cost_usd"] = str(cost)
                        row["ceiling_charge_usd"] = str(max(cost,
                            Decimal(row["ceiling_charge_usd"])))
                except Exception:  # noqa: BLE001 - ignore untrusted optional billing metadata.
                    row["reported_cost_invalid"] = True
        self.save()
