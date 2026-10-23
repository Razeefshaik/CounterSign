"""Browser handoffs that keep the async balance poller running while waiting."""

import asyncio
import re
from collections.abc import Callable

from .redact import Redactor


class ManualSteps:
    def __init__(
        self,
        redactor: Redactor,
        *,
        input_fn: Callable[[str], str] = input,
        output_fn: Callable[[str], None] = print,
    ):
        self.redactor = redactor
        self._input = input_fn
        self._output = output_fn

    def note(self, message: str):
        self._output(self.redactor.text(message))

    async def confirm(self, instructions: str):
        self.note(instructions)
        self.note("Complete the browser action, then press Enter. Do not enter credentials.")
        await asyncio.to_thread(self._input, "> ")

    async def ask_id(self, instructions: str) -> str:
        """Read a PayPal resource ID, never an email, URL, password or token."""
        self.note(instructions)
        while True:
            value = (await asyncio.to_thread(self._input, "> ")).strip()
            if (
                re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", value)
                and self.redactor.text(value) == value
            ):
                return value
            self.note("Enter a resource ID only. Do not enter credentials, URLs or emails.")
