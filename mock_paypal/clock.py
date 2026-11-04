"""Deterministic event scheduler. No wall-clock reads or sleeps."""

import heapq
import inspect
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from itertools import count

START = datetime(2026, 11, 2, 9, tzinfo=UTC)
Callback = Callable[[], Awaitable[None] | None]


def timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


class MockClock:
    def __init__(self, start: datetime = START):
        if start.tzinfo is None or start.utcoffset() is None:
            raise ValueError("Time must include a timezone")
        self.now = start.astimezone(UTC)
        self._sequence = count()
        self._events: list[tuple[datetime, int, Callback]] = []
        self._checkpoints = {}  # C62: data-only descriptors for stopped mock snapshots.
        self._next_sequence = 0  # C62: explicit, portable snapshot counter.

    def schedule(self, seconds: int, callback: Callback, *, checkpoint=None) -> None:
        if seconds < 0:
            raise ValueError("Delay must be nonnegative")
        sequence = next(self._sequence)  # C62: retain event order across a restore.
        self._next_sequence = sequence + 1
        self._checkpoints[sequence] = checkpoint
        heapq.heappush(
            self._events,
            (self.now + timedelta(seconds=seconds), sequence, callback),
        )

    async def set(self, target: datetime) -> None:
        if target.tzinfo is None or target.utcoffset() is None:
            raise ValueError("Time must include a timezone")
        target = target.astimezone(UTC)
        if target < self.now:
            raise ValueError("Clock may only move forward")
        while self._events and self._events[0][0] <= target:
            due, sequence, callback = heapq.heappop(self._events)
            self._checkpoints.pop(sequence, None)  # C62
            self.now = due
            result = callback()
            if inspect.isawaitable(result):
                await result
        self.now = target

    async def advance(self, seconds: int) -> None:
        if seconds < 0:
            raise ValueError("Advance must be nonnegative")
        await self.set(self.now + timedelta(seconds=seconds))
