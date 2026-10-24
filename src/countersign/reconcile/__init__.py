"""Read-only PayPal reconciliation; importing this package opens no resources."""

from .common import ReconcileError, StageResult

__all__ = ["ReconcileError", "StageResult"]
