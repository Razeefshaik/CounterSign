"""C62: data-only snapshots of a quiescent local mock, including its timers.

Only fixed types/event handlers are supported. No code, pickle, caller-selected
paths or uploaded snapshot contents are accepted by the local control routes.
"""

import base64
import heapq
import json
from collections import deque
from dataclasses import fields, is_dataclass
from datetime import datetime
from itertools import count
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from mock_paypal.clock import MockClock
from mock_paypal.config import MockConfig
from mock_paypal.faults import Fault, FaultState
from mock_paypal.payouts import PayoutBatch, PayoutItem, PayoutState
from mock_paypal.resources import ResourceState
from mock_paypal.webhooks import WebhookState

CLASSES = {cls.__name__:cls for cls in (
    PayoutBatch, PayoutItem, PayoutState, ResourceState, WebhookState, Fault, FaultState,
)}
KINDS = frozenset({"forget", "process", "expire", "seed_expire", "seed_process", "refresh", "deliver"})


class CheckpointError(ValueError):
    """Fixed diagnostic; no snapshot values or filesystem paths."""


def encode(value):
    nodes, seen = [], {}

    def visit(item):
        if item is None or type(item) in {str, int, bool}:
            return item
        if id(item) in seen:
            return {"ref":seen[id(item)]}
        index = len(nodes)
        seen[id(item)] = index
        node = {}
        nodes.append(node)
        if isinstance(item, datetime):
            node.update(type="datetime", value=item.isoformat())
        elif isinstance(item, bytes):
            node.update(type="bytes", value=base64.b64encode(item).decode("ascii"))
        elif type(item) is dict:
            node.update(type="dict", value=[[visit(k), visit(v)] for k, v in item.items()])
        elif type(item) in {list, tuple, set, deque}:
            node.update(type=type(item).__name__, value=[visit(v) for v in item])
            if isinstance(item, deque):
                node["maxlen"] = item.maxlen
        elif is_dataclass(item) and type(item).__name__ in CLASSES:
            node.update(type=type(item).__name__, value={f.name:visit(getattr(item, f.name)) for f in fields(item)})
        else:
            raise CheckpointError("MOCK_CHECKPOINT_TYPE_UNSUPPORTED")
        return {"ref":index}

    root = visit(value)
    return {"root":root, "nodes":nodes}


def decode(document):
    nodes, cache = document["nodes"], {}

    def visit(item):
        if item is None or type(item) in {str, int, bool}:
            return item
        index = item["ref"]
        if type(index) is not int or not 0 <= index < len(nodes):
            raise CheckpointError("MOCK_CHECKPOINT_INVALID")
        if index in cache:
            return cache[index]
        node = nodes[index]
        kind, value = node["type"], node["value"]
        if kind == "dict":
            result = cache[index] = {}
            result.update((visit(k), visit(v)) for k, v in value)
        elif kind in {"list", "deque", "set"}:
            result = cache[index] = [] if kind == "list" else deque(maxlen=node["maxlen"]) if kind == "deque" else set()
            for v in value:
                (result.add if kind == "set" else result.append)(visit(v))
        elif kind == "tuple":
            result = tuple(visit(v) for v in value)
        elif kind == "datetime":
            result = datetime.fromisoformat(value)
            if result.utcoffset() is None:
                raise CheckpointError("MOCK_CHECKPOINT_INVALID")
        elif kind == "bytes":
            result = base64.b64decode(value, validate=True)
        elif kind in CLASSES:
            cls = CLASSES[kind]
            if set(value) != {f.name for f in fields(cls)}:
                raise CheckpointError("MOCK_CHECKPOINT_INVALID")
            result = cls(**{k:visit(v) for k, v in value.items()})
        else:
            raise CheckpointError("MOCK_CHECKPOINT_TYPE_UNSUPPORTED")
        cache[index] = result
        return result

    return visit(document["root"])


def snapshot(store):
    events = []
    for due, sequence, _callback in store.clock._events:
        descriptor = store.clock._checkpoints.get(sequence)
        if not descriptor or descriptor.get("kind") not in KINDS:
            raise CheckpointError("MOCK_CHECKPOINT_NOT_QUIESCENT")
        events.append((due, sequence, descriptor))
    data = {k:v for k, v in vars(store).items() if k not in {"clock", "random", "config", "emit", "record_transaction"}}
    return {"format":1, "graph":encode({
        "data":data, "config":store.config.model_dump(), "random":store.random.getstate(),
        "now":store.clock.now, "sequence":store.clock._next_sequence, "events":events,
    })}


def callback(app, event):
    kind, store = event["kind"], app.state.store
    payouts, resources, webhooks = app.state.payout_service, app.state.resource_service, app.state.webhook_service
    if kind == "forget":
        def forget():
            keys = event["state"].sender_keys
            if keys.get(event["sender_id"]) == event["batch_id"]:
                del keys[event["sender_id"]]
        return forget
    if kind == "process":
        return lambda: payouts.process_batch(event["state"], event["batch"])
    if kind in {"expire", "seed_expire", "seed_process"}:
        def transition():
            item, state = event["item"], event["state"]
            target = "PENDING" if kind == "seed_process" else "UNCLAIMED"
            if store.payouts is state and item.status == target and (kind != "expire" or item.revision == event["revision"]):
                payouts.transition(item, ("SUCCESS" if item.payload["receiver"] in state.registered_receivers else "UNCLAIMED") if kind == "seed_process" else "RETURNED")
        return transition
    if kind == "refresh":
        def refresh():
            if store.resources is event["state"] and store.resources.snapshot_generation == event["generation"]:
                resources.snapshot()
                store.clock.schedule(store.config.balance_refresh_seconds, refresh, checkpoint=event)
        return refresh
    if kind == "deliver":
        return lambda: webhooks.deliver(event["state"], event["record"], event["webhook_id"], event["headers"])
    raise CheckpointError("MOCK_CHECKPOINT_EVENT_UNSUPPORTED")


def restore(app, document):
    if document.get("format") != 1:
        raise CheckpointError("MOCK_CHECKPOINT_INVALID")
    value = decode(document["graph"])
    config = MockConfig.model_validate(value["config"])
    for _due, _sequence, event in value["events"]:
        if event.get("kind") not in KINDS:
            raise CheckpointError("MOCK_CHECKPOINT_EVENT_UNSUPPORTED")
    store = app.state.store
    clock = MockClock(value["now"])
    clock._sequence = count(value["sequence"])
    clock._next_sequence = value["sequence"]
    events = [(due, sequence, callback(app, event)) for due, sequence, event in value["events"]]
    heapq.heapify(events)
    clock._events = events
    clock._checkpoints = {sequence:event for _due, sequence, event in value["events"]}
    store.random.setstate(value["random"])
    vars(store).update(value["data"])
    store.config, store.clock = config, clock


def build_router(directory, *, allow_save=False):
    """The name is fixed; callers never supply a path or file contents."""
    router, base = APIRouter(), Path(directory).resolve()

    @router.post("/_mock/checkpoints/friday/{action}")
    async def checkpoint(action: str, request: Request):
        if request.client.host not in {"127.0.0.1", "::1", "localhost"}:
            return JSONResponse({"error":"CHECKPOINT_LOCAL_ONLY"}, status_code=404)
        if request.url.query or await request.body() or action not in ({"save", "load", "validate"} if allow_save else {"load", "validate"}):
            return JSONResponse({"error":"CHECKPOINT_REQUEST_REFUSED"}, status_code=422)
        path = base / "mock.json"
        if path.is_symlink() or path.resolve().parent != base:
            return JSONResponse({"error":"CHECKPOINT_FILE_REFUSED"}, status_code=422)
        async with request.app.state.control_lock:
            try:
                if action == "save":
                    base.mkdir(parents=True, exist_ok=True)
                    temporary = base / "mock.json.tmp"
                    temporary.write_text(json.dumps(snapshot(request.app.state.store)), encoding="utf-8")
                    temporary.replace(path)
                else:
                    document = json.loads(path.read_text(encoding="utf-8"))
                    if action == "load":
                        restore(request.app, document)
                    else:
                        decode(document["graph"])
                return {"checkpoint":"friday", "action":action}
            except Exception:  # noqa: BLE001 - private state/paths never reach HTTP errors.
                return JSONResponse({"error":"MOCK_CHECKPOINT_FAILED"}, status_code=503)
    return router
