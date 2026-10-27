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
    """Pending D3/D7 response choices; confirmed D5/D6 behavior is fixed."""

    create_includes_items: bool = False  # pending D3
    insufficient_funds_status: int = 422  # pending D7


class PayoutError(Exception):
    def __init__(
        self,
        status: int,
        name: str,
        message: str,
        *,
        details=None,
        links=None,
        information_link: str = "https://developer.paypal.com/api/rest/responses/",
    ):
        super().__init__(message)
        self.status = status
        self.name = name
        self.message = message
        self.details = details or []
        self.links = links or []
        self.information_link = information_link


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
    claim_during_cancel: bool = False
    errors: dict | None = None
    fee_minor: int = 0


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
class PayoutState:
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

    def remember_keys(self, batch: PayoutBatch) -> None:
        state = self.store.payouts
        sender_id = batch.sender_header["sender_batch_id"]
        state.sender_keys[sender_id] = batch.id

        def forget():
            if state.sender_keys.get(sender_id) == batch.id:
                del state.sender_keys[sender_id]

        seconds = self.store.config.key_retention_days * 86400
        self.store.clock.schedule(seconds, forget)

    def get_batch(self, batch_id: str) -> PayoutBatch:
        try:
            return self.store.payouts.batches[batch_id]
        except KeyError:
            raise PayoutError(404, "RESOURCE_NOT_FOUND", "Payout batch not found") from None

    def get_item(self, item_id: str) -> PayoutItem:
        try:
            return self.store.payouts.items[item_id]
        except KeyError:
            raise PayoutError(404, "RESOURCE_NOT_FOUND", "Payout item not found") from None

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
        for id in batch.item_ids:
            item = state.items[id]
            self.store.record_transaction(-item.debited_minor, item.transaction_id)
        state.batches[batch.id] = batch

        def process():
            # Reset replaces state and clock, so old events cannot affect a new run.
            if self.store.payouts is not state:
                return
            batch.status = "PROCESSING"
            self.store.emit(
                "PAYMENT.PAYOUTSBATCH.PROCESSING",
                self.create_doc(batch, "http://localhost:8099"),
            )
            stuck = False
            for item_id in batch.item_ids:
                item = state.items[item_id]
                if item.status == "PENDING":
                    rule = self.forced_outcome(item)
                    if rule is None:
                        target = (
                            "SUCCESS"
                            if item.payload["receiver"] in state.registered_receivers
                            else "UNCLAIMED"
                        )
                        self.transition(item, target)
                    elif rule["to"] == "PENDING":
                        item.synthesised, stuck = True, True
                    else:
                        self.transition(item, rule["to"], synthesised=True)
                        if rule.get("errors") is not None:
                            item.errors = deepcopy(rule["errors"])
            if stuck:
                return  # A stuck item keeps its batch PROCESSING; no completion event.
            batch.status = "SUCCESS"  # Batch processed; individual outcomes can differ.
            batch.completed_at = self.store.clock.now
            self.store.emit(
                "PAYMENT.PAYOUTSBATCH.SUCCESS",
                self.create_doc(batch, "http://localhost:8099"),
            )

        self.store.clock.schedule(self.store.config.payout_processing_seconds, process)
        return batch

    def forced_outcome(self, item: PayoutItem) -> dict | None:
        """First /_mock/payout-outcomes rule due for this receiver, or None (M14)."""
        chosen = None
        for rule in self.store.faults.outcomes:
            if rule["receiver"] != item.payload["receiver"]:
                continue
            rule["seen"] += 1
            if chosen is None and rule["seen"] >= rule["nth"] and rule["used"] < rule["count"]:
                rule["used"] += 1
                chosen = rule
        return chosen

    def transition(
        self,
        item: PayoutItem,
        target: str,
        *,
        synthesised: bool = False,
        emit_webhook: bool = True,
    ):
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
        desired_debit = 0 if target in {"FAILED", "RETURNED", "REFUNDED", "REVERSED"} else total
        if target == "UNCLAIMED" and self.store.config.unclaimed_counts_in_available:
            desired_debit = 0
        self.store.payouts.available_minor += item.debited_minor - desired_debit
        if item.debited_minor != desired_debit:
            self.store.record_transaction(item.debited_minor - desired_debit, item.transaction_id)
        item.debited_minor = desired_debit
        item.status = target
        item.processed_at = self.store.clock.now
        if target == "UNCLAIMED" and item.errors is None:
            item.errors = {
                "name": "RECEIVER_UNREGISTERED",
                "message": "The recipient for this payout does not have an account. A link to sign up for an account was sent to the recipient. However, if the recipient does not claim this payout within 30 days, the funds will be returned to your account.",
                "information_link": "https://developer.paypal.com/docs/api/payments.payouts-batch/#errors",
                "details": [],
                "links": [],
            }
        elif target == "SUCCESS":
            item.errors = None
        item.synthesised |= synthesised
        if previous == target:
            return
        if emit_webhook:
            suffix = {"SUCCESS": "SUCCEEDED", "ONHOLD": "HELD"}.get(target, target)
            self.store.emit(
                "PAYMENT.PAYOUTS-ITEM." + suffix,
                self.item_doc(item, "http://localhost:8099"),
            )
        item.revision += 1
        item.processed_at = self.store.clock.now
        if target == "UNCLAIMED":
            state = self.store.payouts
            revision = item.revision

            def expire():
                if (
                    self.store.payouts is state
                    and item.status == "UNCLAIMED"
                    and item.revision == revision
                ):
                    self.transition(item, "RETURNED")

            self.store.clock.schedule(30 * 86400, expire)

    def reprice_unclaimed(self):
        for item in self.store.payouts.items.values():
            if item.status == "UNCLAIMED":
                self.transition(item, "UNCLAIMED")

    def cancel(self, item_id: str) -> PayoutItem:
        item = self.get_item(item_id)
        if item.claim_during_cancel:
            item.claim_during_cancel = False
            self.transition(item, "SUCCESS")
        if item.cancelled:
            raise PayoutError(400, "ITEM_ALREADY_CANCELLED", "Requested item is already cancelled.")
        if item.status != "UNCLAIMED":
            raise PayoutError(
                400, "ITEM_INCORRECT_STATUS", "Only items in Unclaimed status can be cancelled."
            )
        self.transition(item, "RETURNED", emit_webhook=False)
        for suffix in self.store.config.cancel_emits:
            self.store.emit(
                "PAYMENT.PAYOUTS-ITEM." + suffix,
                self.item_doc(item, "http://localhost:8099"),
            )
        item.cancelled = True
        return item

    @staticmethod
    def link(base: str, path: str, rel: str) -> dict:
        return {
            "href": base.rstrip("/") + path,
            "rel": rel,
            "method": "GET",
            "encType": "application/json",
        }

    def item_doc(self, item: PayoutItem, base: str, *, standalone: bool = False) -> dict:
        doc = {
            "payout_item_id": item.id,
            "transaction_status": item.status,
            "payout_batch_id": item.batch_id,
            "payout_item": {
                "recipient_wallet": "PAYPAL",
                "purpose": "GOODS",
                **deepcopy(item.payload),
            },
            "links": [
                self.link(
                    base, f"/v1/payments/payouts-item/{item.id}", "self" if standalone else "item"
                )
            ],
        }
        if item.processed_at is not None and item.status != "PENDING":
            doc.update(
                transaction_id=item.transaction_id,
                activity_id=item.activity_id,
                payout_item_fee=amount(item.fee_minor),
                time_processed=timestamp(item.processed_at),
            )
        if item.errors:
            doc["errors"] = deepcopy(item.errors)
        if standalone:
            doc["sender_batch_id"] = self.get_batch(item.batch_id).sender_header["sender_batch_id"]
            doc["links"].append(self.link(base, f"/v1/payments/payouts/{item.batch_id}", "batch"))
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
            doc["items"] = [self.item_doc(self.get_item(id), base) for id in batch.item_ids]
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
            "funding_source": "BALANCE",
            "amount": amount(
                sum(minor(self.get_item(id).payload["amount"]["value"]) for id in batch.item_ids)
            ),
        }
        if batch.completed_at is not None:
            header.update(
                time_completed=timestamp(batch.completed_at),
                time_closed=timestamp(batch.completed_at),
                fees=amount(sum(self.get_item(id).fee_minor for id in batch.item_ids)),
            )
        start = (page - 1) * page_size
        total = len(batch.item_ids)
        pages = (total + page_size - 1) // page_size

        def link(number: int, rel: str):
            query = (
                f"?page={number}&page_size={page_size}&total_required={str(total_required).lower()}"
            )
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
