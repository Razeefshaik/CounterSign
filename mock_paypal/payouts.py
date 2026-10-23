"""In-memory payout lifecycle, using only the store's mock clock."""

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

from mock_paypal.clock import timestamp
from mock_paypal.payout_models import CreatePayout, amount, minor

if TYPE_CHECKING:
    from mock_paypal.store import MockStore


@dataclass(frozen=True)
class PayoutBehavior:
    """Pending D3/D5/D7 response choices; no sandbox verification is implied."""

    create_includes_items: bool = False  # pending D3
    cancel_item_status: str = "RETURNED"  # pending D5
    insufficient_funds_status: int = 422  # pending D7
    concurrent_duplicate_status: int = 409  # pending D6d
    concurrent_duplicate_name: str = "DUPLICATE_REQUEST_ID"
    duplicate_batch_status: int = 400  # pending D6b
    duplicate_batch_name: str = "USER_BUSINESS_ERROR"
    duplicate_batch_issue: str = "DUPLICATE_SENDER_BATCH_ID"
    replay_current_header: bool = True  # pending D6a
    replay_original_body_wins: bool = True  # pending D6c


class PayoutError(Exception):
    def __init__(
        self, status: int, name: str, message: str, *, details=None, links=None
    ):
        super().__init__(message)
        self.status = status
        self.name = name
        self.message = message
        self.details = details or []
        self.links = links or []


@dataclass
class PayoutItem:
    id: str
    batch_id: str
    payload: dict
    created_at: datetime
    transaction_id: str
    activity_id: str
    status: str = "PENDING"
    debited_minor: int = 0
    processed_at: datetime | None = None
    cancelled: bool = False
    synthesised: bool = False
    revision: int = 0


@dataclass
class PayoutBatch:
    id: str
    sender_header: dict
    item_ids: list[str]
    created_at: datetime
    status: str = "PENDING"
    completed_at: datetime | None = None
    request_id: str | None = None


@dataclass
class RequestKey:
    batch_id: str
    original_body: dict
    initial_header: dict
    in_progress: bool = True


@dataclass
class PayoutState:
    request_keys: dict[str, RequestKey] = field(default_factory=dict)
    sender_keys: dict[str, str] = field(default_factory=dict)
    available_minor: int = 0
    withheld_minor: int = 0
    registered_receivers: set[str] = field(default_factory=set)
    batches: dict[str, PayoutBatch] = field(default_factory=dict)
    items: dict[str, PayoutItem] = field(default_factory=dict)


class PayoutService:
    def __init__(self, store: "MockStore", behavior: PayoutBehavior):
        self.store = store
        self.behavior = behavior

    def remember_keys(
        self, batch: PayoutBatch, request_id: str | None, body: CreatePayout
    ) -> RequestKey:
        state = self.store.payouts
        sender_id = batch.sender_header["sender_batch_id"]
        state.sender_keys[sender_id] = batch.id
        record = RequestKey(
            batch.id,
            body.model_dump(exclude_none=True),
            deepcopy(self.create_doc(batch, "")["batch_header"]),
        )
        if request_id:
            state.request_keys[request_id] = record

        def forget():
            if state.sender_keys.get(sender_id) == batch.id:
                del state.sender_keys[sender_id]
            if request_id and state.request_keys.get(request_id) is record:
                del state.request_keys[request_id]

        seconds = self.store.config.key_retention_days * 86400
        self.store.clock.schedule(seconds, forget)
        return record

    def replay_doc(self, record: RequestKey, base: str) -> dict:
        doc = self.create_doc(self.get_batch(record.batch_id), base)
        if not self.behavior.replay_current_header:
            doc["batch_header"] = deepcopy(record.initial_header)
        return doc

    def get_batch(self, batch_id: str) -> PayoutBatch:
        try:
            return self.store.payouts.batches[batch_id]
        except KeyError:
            raise PayoutError(
                404, "RESOURCE_NOT_FOUND", "Payout batch not found"
            ) from None

    def get_item(self, item_id: str) -> PayoutItem:
        try:
            return self.store.payouts.items[item_id]
        except KeyError:
            raise PayoutError(
                404, "RESOURCE_NOT_FOUND", "Payout item not found"
            ) from None

    def create(self, body: CreatePayout) -> PayoutBatch:
        state = self.store.payouts
        total = sum(minor(item.amount.value) for item in body.items)
        if total > state.available_minor:
            raise PayoutError(
                self.behavior.insufficient_funds_status,
                "INSUFFICIENT_FUNDS",
                "Insufficient available balance",
            )
        batch = PayoutBatch(
            id=self.store.new_id("MOCKB"),
            sender_header=body.sender_batch_header.model_dump(exclude_none=True),
            item_ids=[],
            created_at=self.store.clock.now,
        )
        for payload in body.items:
            item = PayoutItem(
                id=self.store.new_id("MOCKI"),
                batch_id=batch.id,
                payload=payload.model_dump(exclude_none=True),
                created_at=self.store.clock.now,
                transaction_id=self.store.new_id("MOCKT"),
                activity_id=self.store.new_id("MOCKACT"),
                debited_minor=minor(payload.amount.value),
            )
            state.items[item.id] = item
            batch.item_ids.append(item.id)
        state.available_minor -= total
        state.batches[batch.id] = batch

        def process():
            # Reset replaces state and clock, so old events cannot affect a new run.
            if self.store.payouts is not state:
                return
            for item_id in batch.item_ids:
                item = state.items[item_id]
                if item.status == "PENDING":
                    target = (
                        "SUCCESS"
                        if item.payload["receiver"] in state.registered_receivers
                        else "UNCLAIMED"
                    )
                    self.transition(item, target)
            batch.status = "SUCCESS"  # Batch processed; individual outcomes can differ.
            batch.completed_at = self.store.clock.now

        self.store.clock.schedule(self.store.config.payout_processing_seconds, process)
        return batch

    def transition(self, item: PayoutItem, target: str, *, synthesised: bool = False):
        allowed = {
            "PENDING",
            "SUCCESS",
            "UNCLAIMED",
            "ONHOLD",
            "BLOCKED",
            "FAILED",
            "RETURNED",
            "REFUNDED",
            "REVERSED",
        }
        if target not in allowed:
            raise ValueError("Unknown payout item status")
        previous = item.status
        total = minor(item.payload["amount"]["value"])
        desired_debit = (
            0 if target in {"FAILED", "RETURNED", "REFUNDED", "REVERSED"} else total
        )
        if target == "UNCLAIMED" and self.store.config.unclaimed_counts_in_available:
            desired_debit = 0
        self.store.payouts.available_minor += item.debited_minor - desired_debit
        item.debited_minor = desired_debit
        item.status = target
        item.synthesised |= synthesised
        if previous == target:
            return
        item.revision += 1
        item.processed_at = self.store.clock.now
        if target == "UNCLAIMED":
            revision = item.revision

            def expire():
                if item.status == "UNCLAIMED" and item.revision == revision:
                    self.transition(item, "RETURNED")

            self.store.clock.schedule(30 * 86400, expire)

    def reprice_unclaimed(self):
        for item in self.store.payouts.items.values():
            if item.status == "UNCLAIMED":
                self.transition(item, "UNCLAIMED")

    def cancel(self, item_id: str) -> PayoutItem:
        item = self.get_item(item_id)
        if item.cancelled:
            raise PayoutError(
                400, "ITEM_ALREADY_CANCELLED", "Item was already cancelled"
            )
        if item.status != "UNCLAIMED":
            raise PayoutError(
                400, "ITEM_INCORRECT_STATUS", "Only UNCLAIMED items can be cancelled"
            )
        self.transition(item, self.behavior.cancel_item_status)
        item.cancelled = True
        return item

    @staticmethod
    def link(base: str, path: str, rel: str) -> dict:
        return {"href": base.rstrip("/") + path, "rel": rel, "method": "GET"}

    def item_doc(
        self, item: PayoutItem, base: str, *, standalone: bool = False
    ) -> dict:
        doc = {
            "payout_item_id": item.id,
            "transaction_id": item.transaction_id,
            "activity_id": item.activity_id,
            "transaction_status": item.status,
            "payout_item_fee": amount(0),
            "payout_batch_id": item.batch_id,
            "payout_item": deepcopy(item.payload),
            "time_processed": timestamp(item.processed_at)
            if item.processed_at
            else None,
            "links": [self.link(base, f"/v1/payments/payouts-item/{item.id}", "item")],
        }
        if item.status == "UNCLAIMED":
            doc["errors"] = {
                "name": "RECEIVER_UNREGISTERED",
                "message": "Receiver is unregistered",
            }
        if standalone:
            doc["sender_batch_id"] = self.get_batch(item.batch_id).sender_header[
                "sender_batch_id"
            ]
        return doc

    def create_doc(self, batch: PayoutBatch, base: str) -> dict:
        doc = {
            "batch_header": {
                "payout_batch_id": batch.id,
                "batch_status": batch.status,
                "sender_batch_header": deepcopy(batch.sender_header),
            },
            "links": [
                {
                    **self.link(base, f"/v1/payments/payouts/{batch.id}", "self"),
                    "encType": "application/json",
                }
            ],
        }
        if self.behavior.create_includes_items:
            doc["items"] = [
                self.item_doc(self.get_item(id), base) for id in batch.item_ids
            ]
        return doc

    def batch_doc(
        self,
        batch: PayoutBatch,
        base: str,
        page: int,
        page_size: int,
        total_required: bool,
    ) -> dict:
        path = f"/v1/payments/payouts/{batch.id}"
        header = {
            **self.create_doc(batch, base)["batch_header"],
            "time_created": timestamp(batch.created_at),
            "time_completed": timestamp(batch.completed_at)
            if batch.completed_at
            else None,
            "amount": amount(
                sum(
                    minor(self.get_item(id).payload["amount"]["value"])
                    for id in batch.item_ids
                )
            ),
            "fees": amount(0),
        }
        start = (page - 1) * page_size
        total = len(batch.item_ids)
        pages = (total + page_size - 1) // page_size

        def link(number: int, rel: str):
            query = f"?page={number}&page_size={page_size}&total_required={str(total_required).lower()}"
            return self.link(base, path + query, rel)

        links = [link(page, "self")]
        if page < pages:
            links.append(link(page + 1, "next"))
        if page > 1:
            links.append(link(page - 1, "prev"))
        doc = {
            "batch_header": header,
            "items": [
                self.item_doc(self.get_item(id), base)
                for id in batch.item_ids[start : start + page_size]
            ],
            "links": links,
        }
        if total_required:
            doc.update(total_items=total, total_pages=pages)
        return doc
