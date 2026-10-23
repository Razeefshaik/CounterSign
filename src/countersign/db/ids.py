"""Application-generated RFC 9562 UUIDv7 IDs, including on Python 3.12."""

import secrets
import threading
import time
from uuid import UUID

_lock = threading.Lock()
_last_ms = -1
_random = 0


def uuid7() -> UUID:
    """A time-ordered UUID; a process-local counter handles equal or regressing clocks."""
    global _last_ms, _random
    with _lock:
        timestamp = time.time_ns() // 1_000_000
        if timestamp > _last_ms:
            _last_ms = timestamp
            _random = secrets.randbits(74)
        else:
            _random += 1
            if _random >= 1 << 74:
                _last_ms += 1
                _random = 0
        if not 0 <= _last_ms < 1 << 48:
            raise OverflowError("UUIDv7 timestamp is outside its 48-bit range")
        value = (
            (_last_ms << 80)
            | (7 << 76)
            | ((_random >> 62) << 64)
            | (2 << 62)
            | (_random & ((1 << 62) - 1))
        )
        return UUID(int=value)
