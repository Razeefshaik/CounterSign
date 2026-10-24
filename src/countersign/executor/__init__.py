"""The executor: the only package allowed to import the PayPal write adapter."""

from countersign.executor.protocol import Executor, ExecutorError

__all__ = ["Executor", "ExecutorError"]
