"""CONTRACTS 1.0.3 defaults; pending sandbox findings remain configurable."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class MockConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    payout_processing_seconds: int = Field(default=30, ge=0)
    key_retention_days: int = Field(default=30, gt=0)
    duplicate_link_host: Literal["mock", "sandbox"] = "mock"
    unclaimed_counts_in_available: bool = False  # confirmed D4
    dispute_hold: bool = False  # D8/D12: captures freeze, withheld does not move
    evidence_allowed_stages: list[str] | None = None  # D9: status plus actual action link
    balance_refresh_seconds: int = Field(default=10800, gt=0)  # pending D12
    transactions_lag_seconds: int = Field(default=10800, ge=0)
    webhook_delay_seconds: int = Field(default=5, ge=0)  # pending D11
    cancel_emits: list[str] = Field(default_factory=lambda: ["RETURNED"])  # D5
