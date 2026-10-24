"""Isolated PayPal LangChain adapter; explicit credentials travel only over stdin."""

import asyncio
import contextlib
import json
import math
import os
import sys
from pathlib import Path

from countersign.agent.safety import sanitize
from countersign.agent.tools import ReadTool, ToolError, check_toolkit_names
from countersign.config import PayPalSettings

MAX_MESSAGE_BYTES = 262144
_OS_ENVIRONMENT = frozenset(
    {
        "SYSTEMROOT", "WINDIR", "PATH", "PATHEXT", "COMSPEC", "TEMP", "TMP",
        "USERPROFILE", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "HOME", "LANG", "LC_ALL",
    }
)
_CHILD_ERRORS = frozenset(
    {
        "TOOLKIT_DEPENDENCIES_MISSING", "TOOLKIT_VERSION_UNSUPPORTED", "TOOLKIT_SET_MISMATCH",
        "TOOLKIT_CONFIG_INVALID", "TOOLKIT_CALL_FAILED", "TOOLKIT_REQUEST_BLOCKED",
        "INVALID_TOOL_ARGUMENTS", "TOOLKIT_MESSAGE_INVALID",
    }
)


def child_environment(environment=None) -> dict[str, str]:
    """Only operating-system essentials survive; tracing has no inherited keys."""
    source = os.environ if environment is None else environment
    return {key: value for key, value in source.items() if key.upper() in _OS_ENVIRONMENT}


class ToolkitGateway:
    """One child, serialized bounded requests, and no automatic call retries.

    The child loads paypal-agent-toolkit 1.11.0 and its LangChain adapter lazily.
    Constructing this gateway opens neither a socket nor a subprocess.
    """

    def __init__(
        self,
        settings: PayPalSettings,
        *,
        python_executable=None,
        timeout: float = 30,
        process_factory=None,
    ):
        if not isinstance(settings, PayPalSettings):
            raise ToolError("TOOLKIT_CONFIG_INVALID")
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
            raise ToolError("TOOLKIT_CONFIG_INVALID")
        self._settings = settings
        self._python = str(python_executable or sys.executable)
        self._timeout = timeout
        self._spawn = process_factory or asyncio.create_subprocess_exec
        self._process = None
        self._definitions = ()
        self._lock = asyncio.Lock()
        self._opened = False
        self._closed = False

    def __repr__(self):
        return "ToolkitGateway(values=<REDACTED>)"

    async def __aenter__(self):
        await self.open()
        return self

    async def __aexit__(self, *_args):
        await self.aclose()

    async def open(self):
        async with self._lock:
            if self._closed:
                raise ToolError("TOOLKIT_CLOSED")
            if self._opened:
                return self
            try:
                self._process = await self._spawn(
                    self._python,
                    "-I",
                    str(Path(__file__).with_name("toolkit_child.py")),
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                    env=child_environment(),
                    limit=MAX_MESSAGE_BYTES + 1,
                )
                response = await self._exchange(
                    {
                        "op": "open",
                        "settings": {
                            "base_url": self._settings.base_url,
                            "client_id": self._settings.client_id,
                            "client_secret": self._settings.client_secret,
                        },
                    }
                )
                definitions = response.get("tools")
                if not isinstance(definitions, list):
                    raise ToolError("TOOLKIT_SET_MISMATCH")
                check_toolkit_names(item.get("name") for item in definitions)
                if any(
                    type(item.get("description")) is not str
                    or type(item.get("schema")) is not dict
                    or item["schema"].get("type") != "object"
                    for item in definitions
                ):
                    raise ToolError("TOOLKIT_SET_MISMATCH")
                self._definitions = tuple(definitions)
                self._opened = True
                return self
            except BaseException as error:
                await self._stop(force=True)
                if isinstance(error, (asyncio.CancelledError, ToolError)):
                    raise
                raise ToolError("TOOLKIT_START_FAILED") from None

    def read_tools(self) -> tuple[ReadTool, ...]:
        if not self._opened or self._closed:
            raise ToolError("TOOLKIT_NOT_READY")
        result = []
        for definition in self._definitions:
            name = definition["name"]

            async def handler(arguments, *, selected=name):
                return await self._invoke(selected, arguments)

            result.append(
                ReadTool(name, definition["description"], definition["schema"], handler)
            )
        return tuple(result)

    async def _invoke(self, name, arguments):
        check_toolkit_names(item["name"] for item in self._definitions)
        async with self._lock:
            if self._closed or not self._opened:
                raise ToolError("TOOLKIT_CLOSED")
            try:
                response = await self._exchange(
                    {"op": "invoke", "tool": name, "arguments": arguments}
                )
                return sanitize(
                    response.get("output"),
                    secrets=(self._settings.client_id, self._settings.client_secret),
                )
            except BaseException as error:
                await self._stop(force=True)
                if isinstance(error, (asyncio.CancelledError, ToolError)):
                    raise
                raise ToolError("TOOLKIT_CALL_FAILED") from None

    async def _exchange(self, message):
        encoded = json.dumps(message, allow_nan=False).encode("utf-8") + b"\n"
        if len(encoded) > MAX_MESSAGE_BYTES:
            raise ToolError("TOOLKIT_MESSAGE_INVALID")

        async def exchange():
            self._process.stdin.write(encoded)
            await self._process.stdin.drain()
            raw = await self._process.stdout.readline()
            if not raw or len(raw) > MAX_MESSAGE_BYTES:
                raise ToolError("TOOLKIT_MESSAGE_INVALID")
            response = json.loads(raw)
            if not isinstance(response, dict):
                raise ToolError("TOOLKIT_MESSAGE_INVALID")
            if response.get("ok") is not True:
                code = response.get("error")
                raise ToolError(code if code in _CHILD_ERRORS else "TOOLKIT_CALL_FAILED")
            return response

        return await asyncio.wait_for(exchange(), self._timeout)

    async def _stop(self, *, force=False):
        self._closed = True
        self._opened = False
        process, self._process = self._process, None
        if process is None:
            return
        # A partial/timed-out/cancelled exchange poisons the IPC channel. Kill
        # before awaiting cleanup, so even a second cancellation leaves no
        # child executing an orphaned request and no stale answer to consume.
        if force:
            with contextlib.suppress(Exception):
                process.kill()
        with contextlib.suppress(Exception):
            process.stdin.close()
        try:
            await asyncio.wait_for(process.wait(), 2)
        except asyncio.CancelledError:
            with contextlib.suppress(Exception):
                process.kill()
            raise
        except (TimeoutError, OSError, RuntimeError):
            with contextlib.suppress(Exception):
                process.kill()
                await asyncio.wait_for(process.wait(), 2)

    async def aclose(self):
        async with self._lock:
            await self._stop()
