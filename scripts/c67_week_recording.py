"""Opt-in mock-week observer. No prompt, tool, shield or executor changes."""

import asyncio
import json
import os
import re
import time
from datetime import UTC, datetime

from jsonschema import validate

from countersign.agent.models import ModelError
from countersign.agent.safety import sanitize
from countersign.agent.tools import ALLOWED_TOOLS, ToolError
from scripts.bundle_eval_runs import TOKEN, private_guard
from scripts.sandbox_checks.redact import EMAIL

MAX_TURNS = 60
MODEL_FIELDS = ("action", "params", "rationale", "flags", "evidence_refs", "expected_effect")
DECISION_SCHEMA = {"type": "object", "additionalProperties": False,
    "required": ["schema_version", "id", "step", "incident_id", "reads", "model",
                 "absent_model_fields", "shield", "turns", "usage", "seconds", "status"],
    "properties": {
        "schema_version": {"const": 1}, "id": {"type": "string"}, "step": {"type": "string"},
        "incident_id": {"type": "string"}, "proposal_id": {"type": "string"},
        "reads": {"type": "array", "items": {"type": "object",
            "required": ["tool", "arguments", "result_summary"], "properties": {
                "tool": {"enum": sorted(ALLOWED_TOOLS)}, "arguments": {"type": "object"},
                "result_summary": {}}, "additionalProperties": False}},
        "model": {"type": "object", "additionalProperties": False, "properties": {
            **{key: {} for key in MODEL_FIELDS}, "injection_flags": {}, "payee_change_flags": {}}},
        "absent_model_fields": {"type": "array", "items": {"type": "string"}},
        "shield": {"type": "object", "required": ["verdict", "rules", "flags"],
            "properties": {"verdict": {"type": ["string", "null"]}, "rules": {}, "flags": {},
                           "injection_flags": {}, "payee_change_flags": {}}, "additionalProperties": False},
        "turns": {"type": "array", "items": {"type": "integer", "minimum": 1}},
        "usage": {}, "seconds": {"type": "number", "minimum": 0}, "status": {"type": "string"}}}


def public(value, redactor):
    """Existing sanitizers plus the existing bare-key detector, before any write."""
    value = redactor.redact(sanitize(value))
    def clean(item):
        if isinstance(item, str):
            # Public exports omit even masked/alias emails; keys remain unusable.
            item = EMAIL.sub("[REDACTED]", TOKEN.sub("[REDACTED]", item))
            return re.sub(r"\bOPEN_ROUTER_API_KEY[\"']?\s*[:=]\s*[\"']?\S+", "[REDACTED]", item,
                          flags=re.IGNORECASE).replace("<REDACTED>", "[REDACTED]")
        if isinstance(item, dict):
            return {key: clean(child) for key, child in item.items()}
        if isinstance(item, list):
            return [clean(child) for child in item]
        return item
    value = clean(value)
    private_guard(value)
    return value


def result_summary(value):
    """Keep resource facts/counts, excluding buyer messages and arbitrary free text."""
    if isinstance(value, list):
        return {"count": len(value), "items": [result_summary(row) for row in value[:6]]}
    if not isinstance(value, dict):
        return {"kind": type(value).__name__}
    fields = {"id", "dispute_id", "order_id", "payout_item_id", "transaction_id", "status",
        "transaction_status", "dispute_state", "amount", "dispute_amount", "currency", "currency_code",
        "minor", "value", "available", "reserved", "total", "error", "outcome_code", "dispute_outcome",
        "state", "kind", "unreserved", "withheld", "effective_available", "cash_required", "payee_verified"}
    collections = {"items", "disputes", "transactions", "trackers", "nodes", "edges", "balances",
                   "payout_item", "payout_items", "data", "results", "paypal", "protected", "naive",
                   "cash", "payout", "reservations", "makers", "runs", "lines"}
    summary = {}
    for key in fields | collections:
        if key in value:
            child = value[key]
            summary[key] = result_summary(child) if isinstance(child, (dict, list)) else child
    return summary


def load_rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


class WeekRecorder:
    def __init__(self, store):
        self.store = store
        self.decisions = load_rows(store.directory / "decisions.jsonl")
        self.turns = load_rows(store.directory / "turns.jsonl")
        self.current = None
        self.step = "between_steps"
        self.stop_code = None

    def write(self, name, rows):
        # Atomic complete snapshots keep a partial decision durable before its verdict exists.
        safe = public(rows, self.store.redactor)
        if name == "decisions.jsonl":
            for row in safe:
                validate(row, DECISION_SCHEMA)
        path = self.store.directory / name
        temporary = path.with_suffix(".jsonl.tmp")
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            for row in safe:
                handle.write(json.dumps(row, allow_nan=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)

    def save(self):
        self.write("turns.jsonl", self.turns)
        self.write("decisions.jsonl", self.decisions)

    def start_decision(self, request):
        incident = request["data"]["incident"]
        if not request["observations"] or self.current is None:
            self.current = {"schema_version": 1, "id": f"decision-{len(self.decisions) + 1:04d}",
                "step": self.step, "incident_id": incident["id"], "reads": [], "model": {},
                "absent_model_fields": list(MODEL_FIELDS) + ["injection_flags", "payee_change_flags"],
                "shield": {"verdict": None, "rules": None, "flags": None},
                "turns": [], "usage": None, "seconds": 0, "status": "investigating"}
            self.decisions.append(self.current)
        return self.current

    def read(self, name, arguments, result):
        if self.current is not None and name in ALLOWED_TOOLS:
            self.current["reads"].append({"tool": name, "arguments": arguments,
                                          "result_summary": result_summary(result)})
            self.save()

    async def capture(self, engine):
        async with engine.database.tx() as conn:
            cursor = await conn.execute("SELECT v.doc FROM proposals p JOIN v_dash_proposals v ON v.id=p.id "
                "WHERE p.raw_output ? 'agent_trace' ORDER BY p.created_at,p.id")
            documents = [row["doc"] for row in await cursor.fetchall()]
        assigned = {row.get("proposal_id") for row in self.decisions}
        for row in self.decisions:
            matches = [doc for doc in documents if doc["id"] == row.get("proposal_id") or (
                doc["id"] not in assigned and doc["incident_id"] == row["incident_id"]
                and doc["action"] == row["model"].get("action"))]
            if not matches:
                continue
            doc = matches[-1]
            row["proposal_id"] = doc["id"]
            assigned.add(doc["id"])
            flags = doc.get("flags")
            row["shield"] = {"verdict": doc.get("verdict"), "rules": doc.get("rule_results"),
                "flags": flags, "injection_flags": [f for f in flags or [] if f["type"] == "PROMPT_INJECTION"],
                "payee_change_flags": [f for f in flags or [] if f["type"] == "PAYEE_CHANGE_REQUEST"]}
        self.save()


class RecordedModel:
    def __init__(self, inner, recorder):
        self.inner, self.recorder = inner, recorder
        self.model_id = inner.model_id

    def finish_step(self):
        finish = getattr(self.inner, "finish_step", None)
        if callable(finish):
            finish()

    async def complete(self, request):
        recorder = self.recorder
        if recorder.stop_code:
            raise ModelError(recorder.stop_code)
        row = recorder.start_decision(request)
        count = recorder.store.data.get("c67_model_turns", 0)
        if count >= MAX_TURNS:
            recorder.stop_code = "MODEL_WEEK_TURN_CAP_EXHAUSTED"
            row["status"] = recorder.stop_code
            recorder.save()
            raise ModelError(recorder.stop_code)
        # Reserve durably before a model call; a crash cannot erase budget use.
        recorder.store.checkpoint(c67_model_turns=count + 1)
        turn = {"number": count + 1, "decision_id": row["id"], "step": row["step"], "status": "started",
                "at": datetime.now(UTC).isoformat(), "usage": None, "seconds": 0}
        recorder.turns.append(turn)
        row["turns"].append(count + 1)
        recorder.save()
        start = time.monotonic()
        try:
            answer = await self.inner.complete(request)  # Identical request; no retry or prompt alteration.
            turn["status"] = "passed"
            if isinstance(answer, dict) and ("proposal_json" in answer or "tool_calls" not in answer):
                try:
                    proposal = json.loads(answer["proposal_json"]) if "proposal_json" in answer else answer
                except (ValueError, TypeError):
                    proposal = {}
                if isinstance(proposal, dict):
                    row["model"] = {key: proposal[key] for key in MODEL_FIELDS if key in proposal}
                    row["absent_model_fields"] = [key for key in MODEL_FIELDS if key not in proposal]
                    if "flags" in proposal and isinstance(proposal["flags"], list):
                        row["model"].update(injection_flags=[f for f in proposal["flags"]
                            if isinstance(f, dict) and f.get("type") == "PROMPT_INJECTION"],
                            payee_change_flags=[f for f in proposal["flags"]
                            if isinstance(f, dict) and f.get("type") == "PAYEE_CHANGE_REQUEST"])
                    else:
                        row["absent_model_fields"].extend(["injection_flags", "payee_change_flags"])
                row["status"] = "proposed"
                turn["model_return"] = row["model"]
                turn["absent_model_fields"] = list(row["absent_model_fields"])
            return answer
        except (Exception, asyncio.CancelledError) as error:
            code = error.args[0] if isinstance(error, ModelError) and error.args else "MODEL_CALL_FAILED"
            if isinstance(error, asyncio.CancelledError):
                code = "MODEL_TURN_TIMEOUT"
            if not isinstance(code, str) or not re.fullmatch(r"MODEL_[A-Z_]{1,70}", code):
                code = "MODEL_CALL_FAILED"
            recorder.stop_code = code
            turn.update(status="failed", error=code)
            row["status"] = code
            raise
        finally:
            turn["seconds"] = round(time.monotonic() - start, 3)
            usage = getattr(self.inner, "last_usage_metadata", {})
            if isinstance(usage, dict) and all(type(usage.get(k)) is int for k in ("input_tokens", "output_tokens")):
                turn["usage"] = {k: usage[k] for k in ("input_tokens", "output_tokens")}
                turn["usage"]["total_tokens"] = sum(turn["usage"].values())
            client = getattr(self.inner, "_client", None)
            meta = getattr(client, "last_event_metadata", {})
            turn.update({k: meta[k] for k in ("startup_warning_count", "rejected_item_type") if k in meta})
            selected = [t for t in recorder.turns if t["number"] in row["turns"]]
            row["seconds"] = round(sum(t["seconds"] for t in selected), 3)
            row["usage"] = ({k: sum(t["usage"][k] for t in selected)
                for k in ("input_tokens", "output_tokens", "total_tokens")}
                if all(t["usage"] is not None for t in selected) else None)
            recorder.save()


def install(engine, store):
    """Observe the existing prepared session, retaining its tools and timeout."""
    if getattr(store, "c67_recorder", None) is not None:
        return store.c67_recorder
    recorder = store.c67_recorder = WeekRecorder(store)
    commander = engine._agent._commander
    commander.model = RecordedModel(commander.model, recorder)
    original_step, original_read = store.step, commander.tools.invoke

    async def step(name, callback):
        previous, recorder.step = recorder.step, name
        try:
            return await original_step(name, callback)
        finally:
            recorder.step = previous

    async def read(name, arguments):
        try:
            result = await original_read(name, arguments)
        except ToolError as error:
            recorder.read(name, arguments, {"error": error.args[0]})
            raise
        recorder.read(name, arguments, result)
        return result

    store.step, commander.tools.invoke = step, read
    recorder.save()
    return recorder


async def finish_export(engine, store):
    recorder = getattr(store, "c67_recorder", None)
    if recorder is None:
        return
    await recorder.capture(engine)
    snapshot = await engine.snapshot()
    recorder.write("money.json", [{"source": "database views at week stop; no extra engine tick",
        "cash": snapshot["cash"],
        "makers": [{key: row[key] for key in ("id", "display_name", "settled_last_30d", "reserved", "payable")
                    if key in row} for row in snapshot["makers"]],
        "runs": [{key: row[key] for key in ("id", "week_start", "total", "state") if key in row}
                 for row in snapshot["runs"]]}])
