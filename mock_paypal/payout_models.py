"""PayPal payout request shapes and exact decimal-string conversion."""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


def minor(value: str) -> int:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]+\.[0-9]{2}", value):
        raise ValueError("Use a decimal string with two places")
    whole, fraction = value.split(".")
    return int(whole) * 100 + int(fraction)


def amount(value: int) -> dict[str, str]:
    return {"value": f"{value // 100}.{value % 100:02d}", "currency": "USD"}


class PayPalBody(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class Amount(PayPalBody):
    value: str
    currency: Literal["USD"]

    @field_validator("value")
    @classmethod
    def positive_amount(cls, value: str) -> str:
        if minor(value) <= 0:
            raise ValueError("Payout amount must be positive")
        return value


class SenderHeader(PayPalBody):
    sender_batch_id: str = Field(min_length=1)
    email_subject: str | None = None
    email_message: str | None = None


class ItemBody(PayPalBody):
    recipient_type: Literal["EMAIL"]
    amount: Amount
    receiver: str = Field(min_length=1)
    sender_item_id: str = Field(min_length=1)
    note: str | None = None


class CreatePayout(PayPalBody):
    sender_batch_header: SenderHeader
    items: list[ItemBody] = Field(min_length=1, max_length=15000)
