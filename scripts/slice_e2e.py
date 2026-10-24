"""C21's human-operated slice. Importing this module opens no files or connections.

Run with ``python -m scripts.slice_e2e --target mock|sandbox``. Sandbox execution
belongs to the owner; tests inject transports and fake credentials only.
"""

import hashlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx

from countersign.paypal.http import canonical_payload, resource_id
from scripts.sandbox_checks.checks import payout_common as common
from scripts.sandbox_checks.checks.d02_money_in import captures_in

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
            self.checkpoint.record(
                "STAGED_LOST_RESPONSE", request_id=request.headers.get("PayPal-Request-Id")
            )
            raise httpx.ReadTimeout("Staged lost response", request=request)
        return await self.inner.handle_async_request(request)

    async def aclose(self):
        await self.inner.aclose()


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
