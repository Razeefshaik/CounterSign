"""C21's human-operated slice. Importing this module opens no files or connections.

Run with ``python -m scripts.slice_e2e --target mock|sandbox``. Sandbox execution
belongs to the owner; tests inject transports and fake credentials only.
"""

import argparse
import asyncio
import hashlib
import json
import os
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from countersign.config import load_settings
from countersign.harness import Engine, EngineSettings
from countersign.paypal.http import PayPalHTTP, canonical_payload, resource_id
from scripts.sandbox_checks.checks import payout_common as common
from scripts.sandbox_checks.checks.d02_money_in import captures_in
from scripts.sandbox_checks.client import SandboxClient
from scripts.sandbox_checks.env import SandboxSettings
from scripts.sandbox_checks.manual import ManualSteps
from scripts.sandbox_checks.redact import Redactor

PAYOUT_PATH = "/v1/payments/payouts"


class SliceError(RuntimeError):
    """Fixed diagnostic codes, never external exception text or private settings."""


class Checkpoint:
    """Fsynced atomic checkpoints and public append-only evidence, one writer at a time."""

    def __init__(self, directory, *, target, identity, redactor, clock=None, output=print):
        self.directory = Path(directory)
        self.redactor, self.output = redactor, output
        self.clock = clock or (lambda: datetime.now(UTC))
        self.directory.mkdir(parents=True, exist_ok=True)
        self._lock = (self.directory / ".lock").open("a+b")
        self._lock.seek(0)
        if os.fstat(self._lock.fileno()).st_size == 0:
            self._lock.write(b"0")
            self._lock.flush()
        self._lock.seek(0)
        try:
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(self._lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._lock.close()
            raise SliceError("SLICE_ALREADY_RUNNING") from None
        self.path = self.directory / "state.json"
        fingerprint = hashlib.sha256(canonical_payload(identity)).hexdigest()
        try:
            if self.path.exists():
                self.state = json.loads(self.path.read_text(encoding="utf-8"))
                if (self.state["target"], self.state["identity_hash"]) != (target, fingerprint):
                    raise SliceError("SAVED_RUN_SETTINGS_CHANGED")
            else:
                self.state = {
                    "target": target,
                    "identity_hash": fingerprint,
                    "data": {},
                    "steps": {},
                }
                self.save()
        except Exception:
            self.close()
            raise

    @property
    def data(self):
        return self.state["data"]

    def close(self):
        if not self._lock.closed:
            self._lock.close()

    def save(self):
        temporary = self.path.with_suffix(".json.tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(self.redactor.redact(self.state), handle, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.path)

    def checkpoint(self, **updates):
        self.data.update(updates)
        self.save()

    def note(self, message):
        self.output(self.redactor.text(message))

    def record(self, event, **details):
        item = {"at": self.clock().isoformat(), "event": event, **details}
        with (self.directory / "run.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(self.redactor.redact(item), allow_nan=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    async def step(self, name, callback):
        state = self.state["steps"].get(name)
        if state == "SUCCEEDED":
            return
        self.state["steps"][name] = "IN_PROGRESS"
        self.save()  # before any callback can perform a side effect
        await callback()
        self.state["steps"][name] = "SUCCEEDED"
        self.save()
        self.record("STEP_SUCCEEDED", step=name)


class LostResponse(httpx.AsyncBaseTransport):
    """Script-only fault: send the first payout POST, discard its unread response.

    A durable marker is written before forwarding it. On resume the fault is not
    injected again. The executor, rather than this wrapper, owns write recovery.
    """

    def __init__(self, inner, checkpoint):
        self.inner, self.checkpoint = inner, checkpoint

    async def handle_async_request(self, request):
        if (
            request.method == "POST"
            and request.url.path == PAYOUT_PATH
            and not self.checkpoint.data.get("lost_response_armed")
        ):
            self.checkpoint.checkpoint(lost_response_armed=True)
            response = await self.inner.handle_async_request(request)
            # Closing without reading deliberately loses even a successful reply.
            await response.aclose()
            self.checkpoint.checkpoint(lost_response_discarded=True)
            self.checkpoint.record(
                "STAGED_LOST_RESPONSE", request_id=request.headers.get("PayPal-Request-Id")
            )
            raise httpx.ReadTimeout("Staged lost response", request=request)
        return await self.inner.handle_async_request(request)

    async def aclose(self):
        await self.inner.aclose()


class RecoveryGate(httpx.AsyncBaseTransport):
    """Brief staged network outage, so UNKNOWN is observed by the real lifecycle.

    The first POST reaches PayPal. Subsequent payout POSTs fail before sending
    until release(), allowing the next facade tick to discover the payout case.
    No fake successful response or domain state is ever supplied.
    """

    def __init__(self, inner, checkpoint):
        self.inner, self.checkpoint = inner, checkpoint

    async def handle_async_request(self, request):
        if request.method == "POST" and request.url.path == PAYOUT_PATH:
            if self.checkpoint.data.get("payout_sent") and not self.checkpoint.data.get(
                "recovery_released"
            ):
                raise httpx.ReadTimeout("Staged recovery pause before send", request=request)
            if not self.checkpoint.data.get("payout_sent"):
                self.checkpoint.checkpoint(payout_sent=True)
        return await self.inner.handle_async_request(request)

    def release(self):
        self.checkpoint.checkpoint(recovery_released=True)
        self.checkpoint.record("STAGED_RECOVERY_RELEASED")

    async def aclose(self):
        await self.inner.aclose()


def require(condition, code):
    if not condition:
        raise SliceError(code)


def _maker(snapshot, name):
    matches = [maker for maker in snapshot["makers"] if maker["display_name"] == name]
    require(len(matches) == 1, "MAKER_NOT_UNIQUE")
    return matches[0]


def payout_week(now):
    friday = now.date() + timedelta(days=(4 - now.weekday()) % 7)
    return friday - timedelta(days=friday.weekday())


def validate_settled(snapshot, checkpoint):
    require(_maker(snapshot, "Maker B")["settled_last_30d"]["minor"] == 180, "B_NOT_SETTLED")
    require(_maker(snapshot, "Maker A")["reserved"]["minor"] == 180, "A_RESERVE_LOST")
    require(_maker(snapshot, "Maker A")["settled_last_30d"]["minor"] == 0, "A_PAID")
    require(
        len(snapshot["operations"]) == 1 and snapshot["operations"][0]["state"] == "SUCCEEDED",
        "UNEXPECTED_OPERATIONS",
    )
    require(
        any(
            i["id"] == checkpoint.data["payout_incident_id"] and i["state"] == "RESOLVED"
            for i in snapshot["incidents"]
        ),
        "PAYOUT_INCIDENT_NOT_RESOLVED",
    )


async def settled_slice(engine, checkpoint, gate, approve, advance, *, poll_limit=120):
    """Public facade flow after seed/dispute creation. Callbacks perform owner/control I/O."""

    async def plan():
        snapshot = await engine.snapshot()
        require(_maker(snapshot, "Maker A")["reserved"]["minor"] == 180, "A_NOT_RESERVED")
        require(_maker(snapshot, "Maker A")["settled_last_30d"]["minor"] == 0, "A_ALREADY_PAID")
        # Recover a plan committed just before its file checkpoint was lost.
        runs = snapshot["runs"]
        if runs:
            require(len(runs) == 1, "UNEXPECTED_RUNS")
            run_id = runs[0]["id"]
        else:
            run_id = await engine.plan_payout_run(date.fromisoformat(checkpoint.data["week_start"]))
        checkpoint.checkpoint(run_id=run_id)
        [pending] = await engine.pending_approvals()
        require(pending["params"]["run_id"] == run_id, "APPROVAL_RUN_MISMATCH")
        lines = pending["params"]["lines"]
        require(
            len(lines) == 1
            and lines[0]["amount"]["minor"] == 180
            and lines[0]["maker_id"] == _maker(snapshot, "Maker B")["id"],
            "WRONG_PAYOUT_LINE",
        )
        checkpoint.checkpoint(
            proposal_id=pending["id"], payload_hash=pending["approval_request"]["payload_hash"]
        )
        checkpoint.record(
            "RUN_PLANNED",
            run_id=run_id,
            reserved_not_paid_minor=180,
            payout_minor=180,
            payload_hash=checkpoint.data["payload_hash"],
        )

    await checkpoint.step("plan", plan)

    async def owner_approval():
        snapshot = await engine.snapshot()
        proposal = next(
            p for p in snapshot["proposals"] if p["id"] == checkpoint.data["proposal_id"]
        )
        if proposal["state"] == "NEEDS_OWNER":
            checkpoint.note("Owner review: pay Maker B $1.80; Maker A $1.80 remains reserved.")
            checkpoint.note("Approval payload hash: " + checkpoint.data["payload_hash"])
            await approve(checkpoint.data["proposal_id"], checkpoint.data["payload_hash"])
        else:
            require(
                proposal["state"] in {"APPROVED", "EXECUTING", "EXECUTED"}, "APPROVAL_UNAVAILABLE"
            )

    await checkpoint.step("owner_approval", owner_approval)

    async def observe_unknown():
        for _ in range(poll_limit):
            await engine.tick()
            snapshot = await engine.snapshot()
            if any(op["state"] == "UNKNOWN" for op in snapshot["operations"]):
                checkpoint.record("PAYOUT_UNKNOWN")
                return
            await advance()
        raise SliceError("UNKNOWN_NOT_OBSERVED")

    await checkpoint.step("lost_response", observe_unknown)

    async def discover_incident():
        for _ in range(poll_limit):
            await engine.tick()
            snapshot = await engine.snapshot()
            incidents = [i for i in snapshot["incidents"] if i["kind"] == "PAYOUT_ITEM"]
            if incidents:
                require(
                    len(incidents) == 1 and incidents[0]["state"] != "RESOLVED",
                    "EARLY_PAYOUT_CLOSURE",
                )
                checkpoint.checkpoint(payout_incident_id=incidents[0]["id"])
                checkpoint.record("PAYOUT_INCIDENT_PENDING", incident_id=incidents[0]["id"])
                return
            await advance()
        raise SliceError("PAYOUT_INCIDENT_NOT_DISCOVERED")

    await checkpoint.step("incident_pending", discover_incident)
    if not checkpoint.data.get("recovery_released"):
        gate.release()

    async def reconcile():
        for _ in range(poll_limit):
            await engine.tick()
            snapshot = await engine.snapshot()
            payout = next(
                i for i in snapshot["incidents"] if i["id"] == checkpoint.data["payout_incident_id"]
            )
            if payout["state"] == "RESOLVED":
                validate_settled(snapshot, checkpoint)
                checkpoint.record(
                    "SLICE_RECONCILED",
                    payout_incident_id=payout["id"],
                    maker_b_settled_minor=180,
                    maker_a_reserved_minor=180,
                )
                return
            await advance()
        raise SliceError("SLICE_PENDING_RECONCILIATION")

    await checkpoint.step("reconciled", reconcile)
    snapshot = await engine.snapshot()
    validate_settled(snapshot, checkpoint)
    return snapshot


class ExchangeRecorder:
    """Sandbox helpers' recorder interface, rooted directly in this slice's log."""

    def __init__(self, checkpoint):
        self.checkpoint, self.redactor = checkpoint, checkpoint.redactor

    def record(self, **exchange):
        # OAuth values are discovered by Redactor before any exchange is persisted.
        exchange.pop("t_sent", None)
        exchange.pop("t_recv", None)
        self.checkpoint.record("SANDBOX_EXCHANGE", **exchange)


class OrderContext:
    """Minimal sandbox harness context; reuse its bound, checkpointed order writes."""

    def __init__(self, checkpoint, client, manual):
        self.checkpoint_store, self.client, self.manual = checkpoint, client, manual
        self.recorder, self.clock = client.recorder, checkpoint.clock

    @property
    def data(self):
        return self.checkpoint_store.data

    def checkpoint(self, **updates):
        self.checkpoint_store.checkpoint(**updates)


async def sandbox_orders(checkpoint, client, manual):
    """Two approved orders, not D2's three-order experiment; reuse its safe helpers."""
    ctx = OrderContext(checkpoint, client, manual)
    orders = dict(checkpoint.data.get("orders", {}))
    for maker in ("A", "B"):
        if maker not in orders:
            result = await common.idempotent_write(
                ctx,
                "create_" + maker,
                "/v2/checkout/orders",
                {
                    "intent": "CAPTURE",
                    "purchase_units": [
                        {
                            "reference_id": "slice-" + maker,
                            "description": "Staged sandbox action: CounterSign vertical slice",
                            "amount": {"currency_code": "USD", "value": "2.00"},
                        }
                    ],
                },
            )
            body = result.get("response") or {}
            if result["state"] != "SUCCEEDED" or not body.get("id"):
                raise SliceError("ORDER_CREATE_UNCONFIRMED")
            orders[maker] = {
                "order_id": resource_id(body["id"]),
                "approval_url": next(
                    (
                        link["href"]
                        for link in body.get("links", [])
                        if link.get("rel") in {"approve", "payer-action"}
                    ),
                    None,
                ),
            }
            checkpoint.checkpoint(orders=orders)
        order = orders[maker]
        if not order.get("capture_id"):
            response = await client.request(
                "GET", "/v2/checkout/orders/" + order["order_id"], step="inspect_" + maker
            )
            body = common.payload(response)
            if response.status_code != 200:
                raise SliceError("ORDER_GET_UNAVAILABLE")
            if body.get("status") not in {"APPROVED", "COMPLETED"}:
                url = order.get("approval_url") or ""
                if not url.startswith("https://www.sandbox.paypal.com/"):
                    raise SliceError("SANDBOX_APPROVAL_URL_UNAVAILABLE")
                await manual.confirm(
                    f"Staged sandbox action: buyer-1 approves Maker {maker}'s $2 order at {url}"
                )
                response = await client.request(
                    "GET", "/v2/checkout/orders/" + order["order_id"], step="approved_" + maker
                )
                body = common.payload(response)
                if response.status_code != 200 or body.get("status") not in {
                    "APPROVED",
                    "COMPLETED",
                }:
                    raise SliceError("BUYER_APPROVAL_UNCONFIRMED")
            captures = captures_in(body)
            if not captures:
                result = await common.idempotent_write(
                    ctx, "capture_" + maker, f"/v2/checkout/orders/{order['order_id']}/capture", {}
                )
                if result["state"] != "SUCCEEDED":
                    raise SliceError("CAPTURE_UNCONFIRMED")
                captures = captures_in(result.get("response") or {})
            if len(captures) != 1 or captures[0].get("status") != "COMPLETED":
                raise SliceError("CAPTURE_UNCONFIRMED")
            order["capture_id"] = resource_id(captures[0]["id"])
            checkpoint.checkpoint(orders=orders)
        checkpoint.record(
            "ORDER_CAPTURE", maker=maker, order_id=order["order_id"], capture_id=order["capture_id"]
        )
    return orders


def seed_state(emails, orders, now):
    return {
        "policy": {"maker_share_pct": 90},
        "makers": [
            {
                "key": key,
                "display_name": "Maker " + key,
                "payout_email": emails[key],
                "verified": True,
            }
            for key in ("A", "B")
        ],
        "orders": [
            {
                "key": "slice-" + key,
                "maker": key,
                "amount": {"minor": 200, "currency": "USD"},
                "capture_id": orders[key]["capture_id"],
                "paypal_order_id": orders[key]["order_id"],
                "captured_at": now.isoformat(),
                "tracking": None,
            }
            for key in ("A", "B")
        ],
        "prior_payouts": [],
        "disputes": [],
    }


def resource_refs(body):
    """Whitelist resource IDs, never write arbitrary response bodies to the slice log."""
    found = {}
    if not isinstance(body, dict):
        return found
    for key in ("payout_item_id", "payout_batch_id", "dispute_id"):
        if isinstance(body.get(key), str):
            found[key] = resource_id(body[key])
    header = body.get("batch_header")
    if isinstance(header, dict) and header.get("payout_batch_id"):
        found["payout_batch_id"] = resource_id(header["payout_batch_id"])
    items = body.get("items")
    if isinstance(items, list):
        found["items"] = [resource_refs(item) for item in items if isinstance(item, dict)]
    return found


def record_refs(store, path, status, refs):
    if not refs:
        return
    store.record("PAYPAL_RESOURCES", path=path, status=status, **refs)
    batches, items = set(store.data.get("batch_ids", [])), set(store.data.get("item_ids", []))

    def collect(value):
        if value.get("payout_batch_id"):
            batches.add(value["payout_batch_id"])
        if value.get("payout_item_id"):
            items.add(value["payout_item_id"])
        for child in value.get("items", []):
            collect(child)

    collect(refs)
    store.checkpoint(batch_ids=sorted(batches), item_ids=sorted(items))


class ResourceLog(httpx.AsyncBaseTransport):
    def __init__(self, inner, checkpoint):
        self.inner, self.checkpoint = inner, checkpoint

    async def handle_async_request(self, request):
        response = await self.inner.handle_async_request(request)
        if request.method == "GET" and request.url.path.startswith(
            ("/v1/payments/payouts", "/v1/customer/disputes")
        ):
            # Read GETs through httpx's content decoding (including gzip). The
            # payout POST remains untouched for LostResponse to discard unread.
            await response.aread()
            try:
                refs = resource_refs(response.json())
            except ValueError:
                refs = {}
            if refs:
                record_refs(self.checkpoint, request.url.path, response.status_code, refs)
        return response

    async def aclose(self):
        await self.inner.aclose()


def local_app_url(value):
    parsed = urlsplit(value)
    require(
        parsed.scheme in {"http", "https"}
        and parsed.hostname in {"localhost", "127.0.0.1"}
        and parsed.path in {"", "/"}
        and not parsed.username
        and not parsed.password
        and not parsed.query
        and not parsed.fragment,
        "APP_URL_MUST_BE_LOOPBACK",
    )
    return value.rstrip("/")


async def check_webhook(http, webhook_id, callback_url):
    require(bool(webhook_id), "WEBHOOK_ID_REQUIRED")
    # Listing works on both the current mock and sandbox; no registration writes.
    response = await http.request("GET", "/v1/notifications/webhooks")
    require(
        response.status_code == 200 and isinstance(response.body, dict),
        "WEBHOOK_REGISTRY_UNAVAILABLE",
    )
    matches = [item for item in response.body.get("webhooks", []) if item.get("id") == webhook_id]
    require(len(matches) == 1 and matches[0].get("url") == callback_url, "WEBHOOK_ID_URL_MISMATCH")
    events = {e.get("name") for e in matches[0].get("event_types", [])}
    require(
        "*" in events or {"CUSTOMER.DISPUTE.CREATED", "PAYMENT.PAYOUTS-ITEM.SUCCEEDED"} <= events,
        "REQUIRED_WEBHOOK_EVENTS_MISSING",
    )


async def webhook_agrees(engine, store, *, advance, poll_limit=120):
    """Owner-approved exception: exact inbox evidence, read-only and parameterised."""
    require(
        len(store.data.get("batch_ids", [])) == 1 and len(store.data.get("item_ids", [])) == 1,
        "RUN_PAYOUT_IDS_NOT_UNIQUE",
    )
    pairs = [
        ("PAYMENT.PAYOUTS-ITEM.SUCCEEDED", store.data["item_ids"][0]),
        ("PAYMENT.PAYOUTSBATCH.SUCCESS", store.data["batch_ids"][0]),
    ]
    for _ in range(poll_limit):
        await engine.tick()
        async with engine.database.tx() as conn:
            for event_type, resource in pairs:
                cursor = await conn.execute(
                    "SELECT event_id,signature,status FROM webhook_inbox "
                    "WHERE event_type=%s AND resource_id=%s ORDER BY received_at,event_id",
                    (event_type, resource),
                )
                for event in await cursor.fetchall():
                    if event["signature"] == "SUCCESS" and event["status"] == "PROCESSED":
                        store.record(
                            "WEBHOOK_GET_AGREEMENT",
                            event_id=event["event_id"],
                            event_type=event_type,
                            resource_id=resource,
                            signature="SUCCESS",
                            status="PROCESSED",
                        )
                        return
        await advance()
    raise SliceError("EXACT_PAYOUT_WEBHOOK_PENDING")


async def drive(engine, store, gate, api, http, *, target, manual, control=None, poll_limit=120):
    """Same scenario for both targets; only staged external controls differ."""

    async def tick_until_reserved():
        for _ in range(poll_limit):
            await engine.tick()
            snapshot = await engine.snapshot()
            if _maker(snapshot, "Maker A")["reserved"]["minor"] == 180:
                return
            await advance()
        raise SliceError("DISPUTE_RESERVE_NOT_CONFIRMED")

    async def advance():
        if target == "mock":
            response = await control.post("/_mock/clock/advance", json={"seconds": 10})
            require(response.status_code == 200, "MOCK_ADVANCE_FAILED")
        else:
            await asyncio.sleep(30)

    async def dispute():
        if target == "mock":
            response = await control.post(
                "/_mock/disputes",
                json={
                    "capture_id": store.data["orders"]["A"]["capture_id"],
                    "reason": "MERCHANDISE_OR_SERVICE_NOT_RECEIVED",
                    "amount": "2.00",
                },
            )
            require(response.status_code == 200, "MOCK_DISPUTE_FAILED")
            dispute_id = resource_id(response.json()["dispute_id"])
        else:
            dispute_id = await manual.ask_id(
                "Staged sandbox action: buyer-1 opens an item-not-received dispute "
                "in the Resolution Center on capture "
                + store.data["orders"]["A"]["capture_id"]
                + ". Enter its PayPal dispute ID; do not open a second case on resume."
            )
        store.checkpoint(dispute_id=dispute_id)
        store.record(
            "STAGED_DISPUTE",
            dispute_id=dispute_id,
            capture_id=store.data["orders"]["A"]["capture_id"],
        )

    # The staged mock control is not automatically repeated after an uncertain POST.
    async def open_dispute():
        if store.data.get("dispute_id"):
            return
        if target == "mock" and store.data.get("dispute_write_sent"):
            raise SliceError("DISPUTE_CREATE_UNCONFIRMED_OWNER_REVIEW_REQUIRED")
        store.checkpoint(dispute_write_sent=True)
        await dispute()

    await store.step("dispute", open_dispute)
    response = await http.request("GET", "/v1/customer/disputes/" + store.data["dispute_id"])
    body = response.body if response.status_code == 200 else {}
    require(
        isinstance(body, dict)
        and body.get("dispute_id") == store.data["dispute_id"]
        and any(
            t.get("seller_transaction_id") == store.data["orders"]["A"]["capture_id"]
            for t in body.get("disputed_transactions", [])
        ),
        "DISPUTE_CAPTURE_MISMATCH",
    )
    await store.step("reserved", tick_until_reserved)

    async def arm_fault():
        if target == "mock":
            require(
                not store.data.get("fault_write_sent"),
                "FAULT_ARM_UNCONFIRMED_OWNER_REVIEW_REQUIRED",
            )
            store.checkpoint(fault_write_sent=True)
            response = await control.post(
                "/_mock/faults",
                json={
                    "match": {
                        "method": "POST",
                        "path": "^/v1/payments/payouts$",
                        "nth": 1,
                        "count": 1,
                    },
                    "action": {"type": "drop_response", "after_commit": True},
                },
            )
            require(response.status_code == 201, "MOCK_FAULT_FAILED")
        store.record("STAGED_FAULT_ARMED", target=target)

    await store.step("fault_armed", arm_fault)

    async def approve(proposal_id, payload_hash):
        if target == "sandbox":
            await manual.confirm(
                "Owner: review the printed run and exact hash. Enter approves this payload through the owner endpoint."
            )
        response = await api.post(
            f"/api/v1/proposals/{proposal_id}/approve",
            json={"payload_hash": payload_hash, "approved_by": "slice-owner"},
        )
        require(response.status_code == 201, "OWNER_APPROVAL_NOT_ACCEPTED")

    result = await settled_slice(engine, store, gate, approve, advance, poll_limit=poll_limit)
    if target == "mock":
        truth = (await control.get("/_mock/ground-truth")).json()
        require(
            len(truth["payout_items"]) == 1 and truth["payout_items"][0]["status"] == "SUCCESS",
            "MOCK_PAYOUT_COUNT_OR_STATE_MISMATCH",
        )
        store.record("MOCK_GROUND_TRUTH", **resource_refs(truth["payout_items"][0]))
    return result


def runtime_settings(target, env_file=".env"):
    # The only .env access is the established runtime loader, never an inspection.
    from dotenv import load_dotenv

    if target == "sandbox":
        load_dotenv(dotenv_path=env_file, override=False, interpolate=False)
    settings = load_settings()
    require(settings.paypal.is_mock == (target == "mock"), "TARGET_SETTINGS_MISMATCH")
    emails = (
        {key: "maker-" + key.lower() + "@example.test" for key in ("A", "B")}
        if target == "mock"
        else {key: os.environ.get("SBX_MAKER_" + key + "_EMAIL", "").strip() for key in ("A", "B")}
    )
    require(
        all("@" in email and email.strip() == email for email in emails.values()),
        "MAKER_EMAILS_REQUIRED",
    )
    require(len(set(emails.values())) == 2, "MAKERS_MUST_BE_DISTINCT")
    return settings, emails


def webhook_app():
    """uvicorn factory: real application routes, workers off for controlled slice ticks.

    The CLI's transport cannot intercept another process's payout POST. Keep this
    receiver/owner API running on the tunnelled port and let only the CLI tick.
    """
    from countersign.main import create_app

    target = os.environ.get("COUNTERSIGN_SLICE_TARGET", "sandbox")
    require(target in {"mock", "sandbox"}, "INVALID_SLICE_TARGET")
    settings, _ = runtime_settings(target, os.environ.get("COUNTERSIGN_SLICE_ENV_FILE", ".env"))
    return create_app(settings=settings, start_worker=False)


async def run_cli(args):
    settings, emails = runtime_settings(args.target, args.env_file)
    app_url = local_app_url(args.app_url)
    now = datetime.now(UTC)
    run_date = date.fromisoformat(args.date) if args.date else now.date()
    redactor = Redactor(
        secrets=(
            settings.database_url,
            settings.owner_token,
            settings.paypal.client_id,
            settings.paypal.client_secret,
        ),
        email_aliases={
            email: "maker-" + key.lower() + "@redacted" for key, email in emails.items()
        },
    )
    store = Checkpoint(
        Path(args.root) / run_date.isoformat(),
        target=args.target,
        identity={
            "database": settings.database_url,
            "base": settings.paypal.base_url,
            "client": settings.paypal.client_id,
            "webhook": settings.paypal.webhook_id,
            "app_url": app_url,
            "emails": emails,
        },
        redactor=redactor,
    )
    try:
        if "started_at" not in store.data:
            store.checkpoint(started_at=now.isoformat(), week_start=payout_week(now).isoformat())
        started = datetime.fromisoformat(store.data["started_at"])
        manual = ManualSteps(redactor)
        raw = ResourceLog(httpx.AsyncHTTPTransport(retries=0, trust_env=False), store)
        transport = LostResponse(raw, store) if args.target == "sandbox" else raw
        gate = RecoveryGate(transport, store)
        config = EngineSettings(
            settings.database_url,
            settings.paypal.base_url,
            settings.paypal.client_id,
            settings.paypal.client_secret,
            settings.paypal.webhook_id,
            lambda: datetime.now(UTC),
            paypal_behavior=settings.paypal.behavior,
        )
        async with (
            PayPalHTTP(settings.paypal, transport=gate) as http,
            httpx.AsyncClient(
                base_url=app_url,
                headers={"X-Owner-Token": settings.owner_token},
                trust_env=False,
                follow_redirects=False,
                timeout=30,
            ) as api,
            httpx.AsyncClient(
                base_url=settings.paypal.base_url,
                trust_env=False,
                follow_redirects=False,
                timeout=30,
            ) as control,
            Engine(config, http=http) as engine,
        ):
            meta = await api.get("/api/v1/meta")
            require(
                meta.status_code == 200
                and meta.json().get("paypal_env")
                == ("sandbox" if args.target == "sandbox" else "mock"),
                "APP_TARGET_MISMATCH",
            )
            store.note(
                "The CounterSign app must receive webhooks on this port, not the day-one catcher. "
                "Use the slice webhook_app factory with workers disabled; do not run another executor."
            )
            callback = app_url + "/webhooks/paypal"
            if args.target == "sandbox":
                parsed = urlsplit(args.tunnel_url or "")
                require(
                    parsed.scheme == "https"
                    and parsed.hostname
                    and not parsed.username
                    and not parsed.password
                    and not parsed.query
                    and not parsed.fragment
                    and parsed.path in {"", "/"},
                    "HTTPS_TUNNEL_URL_REQUIRED",
                )
                callback = args.tunnel_url.rstrip("/") + "/webhooks/paypal"
            await check_webhook(http, settings.paypal.webhook_id, callback)

            async def orders():
                initial = await engine.snapshot()
                require(
                    not any(
                        initial[k]
                        for k in ("makers", "runs", "operations", "disputes", "incidents")
                    ),
                    "DEDICATED_EMPTY_SLICE_DATABASE_REQUIRED",
                )
                if args.target == "sandbox":
                    sandbox = SandboxSettings(
                        settings.paypal.client_id,
                        settings.paypal.client_secret,
                        database_url=settings.database_url,
                    )
                    async with SandboxClient(sandbox, ExchangeRecorder(store)) as client:
                        await sandbox_orders(store, client, manual)
                else:
                    if not store.data.get("orders"):
                        store.checkpoint(
                            orders={
                                key: {
                                    "order_id": "SLICE-O-"
                                    + key
                                    + "-"
                                    + common.uuid7(store.clock).hex,
                                    "capture_id": "SLICE-C-"
                                    + key
                                    + "-"
                                    + common.uuid7(store.clock).hex,
                                }
                                for key in ("A", "B")
                            }
                        )
                    truth = (await control.get("/_mock/ground-truth")).json()
                    require(
                        not truth["payout_items"] and not store.data.get("mock_seed_sent"),
                        "FRESH_MOCK_REQUIRED",
                    )
                    store.checkpoint(mock_seed_sent=True)
                    response = await control.post(
                        "/_mock/seed",
                        json={
                            "balances": {"available": "100.00"},
                            "registered_receivers": list(emails.values()),
                            "captures": [
                                {"id": o["capture_id"], "order_id": o["order_id"], "amount": "2.00"}
                                for o in store.data["orders"].values()
                            ],
                        },
                    )
                    require(response.status_code == 200, "MOCK_SEED_FAILED")
                    for key, order in store.data["orders"].items():
                        store.record("ORDER_CAPTURE", maker=key, **order)

            await store.step("orders", orders)

            async def seed():
                # Engine.seed derives due dates from its live clock. Do not seed
                # new shares into a different week after a long browser pause.
                existing = await engine.snapshot()
                require(
                    bool(existing["makers"])
                    or store.data["week_start"] == payout_week(datetime.now(UTC)).isoformat(),
                    "UNSEEDED_RUN_WEEK_CHANGED_OWNER_REVIEW",
                )
                await engine.seed(seed_state(emails, store.data["orders"], started))

            await store.step("seed", seed)
            await drive(
                engine,
                store,
                gate,
                api,
                http,
                target=args.target,
                manual=manual,
                control=control,
                poll_limit=args.poll_limit,
            )
            # Every payout ID comes from actual GETs; never recover one by sender key guessing.
            if args.target == "sandbox":
                require(
                    store.data.get("lost_response_discarded"), "STAGED_LOST_RESPONSE_NOT_CONFIRMED"
                )
                require(len(store.data.get("batch_ids", [])) == 1, "ONE_BATCH_NOT_CONFIRMED")
                batch = await http.request("GET", PAYOUT_PATH + "/" + store.data["batch_ids"][0])
                require(
                    batch.status_code == 200 and len(batch.body.get("items", [])) == 1,
                    "ONE_ITEM_NOT_CONFIRMED",
                )
                [item] = batch.body["items"]
                detail = await http.request(
                    "GET", "/v1/payments/payouts-item/" + resource_id(item["payout_item_id"])
                )
                require(
                    detail.status_code == 200
                    and detail.body.get("transaction_status") == "SUCCESS",
                    "ITEM_SUCCESS_NOT_CONFIRMED",
                )
                store.record("SANDBOX_ONE_ITEM_CONFIRMED", **resource_refs(detail.body))

            async def advance_proof():
                if args.target == "mock":
                    response = await control.post("/_mock/clock/advance", json={"seconds": 10})
                    require(response.status_code == 200, "MOCK_ADVANCE_FAILED")
                else:
                    await asyncio.sleep(30)

            async def inbox_proof():
                await webhook_agrees(
                    engine, store, advance=advance_proof, poll_limit=args.poll_limit
                )

            await store.step("webhooks_agree", inbox_proof)
            validate_settled(await engine.snapshot(), store)
            store.note(
                "Slice complete: Maker B settled once; payout incident reconciled; Maker A remains reserved."
            )
    finally:
        store.close()


async def serve_cli(args):
    """Receiver and owner routes only; the separate slice CLI owns every tick."""
    import uvicorn

    from countersign.main import create_app

    settings, _ = runtime_settings(args.target, args.env_file)
    app = create_app(settings=settings, start_worker=False)
    await uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=args.port, access_log=False, log_level="warning")
    ).serve()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True, choices=("mock", "sandbox"))
    parser.add_argument("--app-url", default="http://127.0.0.1:8000")
    parser.add_argument("--tunnel-url")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--root", default="recordings/slice")
    parser.add_argument("--date", help="UTC run date to resume, YYYY-MM-DD")
    parser.add_argument("--poll-limit", type=int, default=120)
    parser.add_argument("--serve", action="store_true", help="serve receiver/owner routes only")
    parser.add_argument(
        "--port", type=int, default=8000, help="loopback receiver port with --serve"
    )
    args = parser.parse_args(argv)
    try:
        require(1 <= args.poll_limit <= 1000, "INVALID_POLL_LIMIT")
        require(1 <= args.port <= 65535, "INVALID_RECEIVER_PORT")
        with asyncio.Runner(
            loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None
        ) as runner:
            runner.run(serve_cli(args) if args.serve else run_cli(args))
    except KeyboardInterrupt:
        print("Slice interrupted; saved progress remains available for resume.")
        return 130
    except Exception as exc:  # noqa: BLE001 - no private driver/HTTP exception text
        print(str(exc) if isinstance(exc, SliceError) else "SLICE_STOPPED_REVIEW_CHECKPOINT")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
