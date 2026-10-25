"""CONTRACTS 1.0.3 defaults; pending sandbox findings remain configurable."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class MockConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    payout_processing_seconds: int = Field(default=30, ge=0)
    key_retention_days: int = Field(default=30, gt=0)
    duplicate_link_host: Literal["mock", "sandbox"] = "mock"
    unclaimed_counts_in_available: bool = False  # pending D4
    dispute_hold: bool = True  # pending D8c
    evidence_allowed_stages: list[str] = Field(
        default_factory=lambda: ["INQUIRY", "CHARGEBACK"]
    )  # pending D9
    balance_refresh_seconds: int = Field(default=10800, gt=0)  # pending D12
    transactions_lag_seconds: int = Field(default=10800, ge=0)
    webhook_delay_seconds: int = Field(default=5, ge=0)  # pending D11
    cancel_emits: list[str] = Field(
        default_factory=lambda: ["CANCELED", "RETURNED"]
    )  # pending D5
