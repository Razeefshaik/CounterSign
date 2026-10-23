"""Public, redacted exchange recordings; optional originals stay in raw/."""

import base64
import json
import re
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path

from .redact import AUTHORIZATION_KEYS, Redactor, normalized_key


def utc_now() -> datetime:
    return datetime.now(UTC)


def timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("Recording timestamps must be timezone-aware.")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _raw_json(value):
    if isinstance(value, bytes):
        return {"encoding": "base64", "data": base64.b64encode(value).decode("ascii")}
    raise TypeError("Unsupported raw recording value type.")


class Recorder:
    def __init__(
        self,
        check_id: str,
        *,
        redactor: Redactor,
        root: str | Path = "recordings/sandbox",
        retain_raw: bool = False,
        clock: Callable[[], datetime] = utc_now,
    ):
        if not re.fullmatch(r"D(?:[1-9]|1[0-3])", check_id):
            raise ValueError("Check ID must be D1 through D13.")
        self.check_id = check_id
        self.redactor = redactor
        self.root = Path(root)
        self.retain_raw = retain_raw
        self._clock = clock

    def record(
        self,
        *,
        step: str,
        method: str,
        path: str,
        request_headers: Mapping[str, str] | None = None,
        request_body=None,
        response_status: int | None = None,
        response_headers: Mapping[str, str] | None = None,
        response_body=None,
        t_sent: datetime | None = None,
        t_recv: datetime | None = None,
        error: str | None = None,
    ) -> Path:
        sent = t_sent if t_sent is not None else self._clock()
        received = t_recv if t_recv is not None else self._clock()
        exchange = {
            "method": method.upper(),
            "path": path,
            "request_headers": {
                key: value
                for key, value in (request_headers or {}).items()
                if normalized_key(key) not in AUTHORIZATION_KEYS
            },
            "request_body": request_body,
            "response_status": response_status,
            "response_headers": {
                key: value
                for key, value in (response_headers or {}).items()
                if normalized_key(key) not in AUTHORIZATION_KEYS
            },
            "response_body": response_body,
            "t_sent": timestamp(sent),
            "t_recv": timestamp(received),
        }
        if error is not None:
            exchange["error"] = error
        public_json = json.dumps(
            self.redactor.redact(exchange), ensure_ascii=False, indent=2, allow_nan=False
        ) + "\n"
        date = sent.astimezone(UTC).date().isoformat()
        directory = self.root / date / self.check_id
        directory.mkdir(parents=True, exist_ok=True)
        slug = re.sub(r"[^A-Za-z0-9_-]+", "_", self.redactor.text(step)).strip("_")[:80] or "step"
        sequence = max(
            (
                int(match.group(1))
                for entry in directory.iterdir()
                if (match := re.match(r"^(\d+)_", entry.name))
            ),
            default=0,
        ) + 1
        while True:
            destination = directory / f"{sequence:02d}_{slug}.json"
            try:
                with destination.open("x", encoding="utf-8") as handle:
                    handle.write(public_json)
                break
            except FileExistsError:
                sequence += 1
        if self.retain_raw:
            raw_directory = self.root / "raw" / date / self.check_id
            raw_directory.mkdir(parents=True, exist_ok=True)
            with (raw_directory / destination.name).open("x", encoding="utf-8") as handle:
                json.dump(exchange, handle, ensure_ascii=False, indent=2, default=_raw_json)
                handle.write("\n")
        return destination
