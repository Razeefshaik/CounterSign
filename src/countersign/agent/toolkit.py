"""Isolated PayPal LangChain adapter; explicit credentials travel only over stdin."""

import asyncio
import contextlib
import io
import json
import math
import os
import subprocess
import sys
import threading
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


class _ThreadWriter:
    """Match asyncio's writer surface without writing a pipe on the event loop."""

    def __init__(self, stream, limit):
        self._stream = stream
        self._limit = limit
        self._pending = bytearray()

    def write(self, data):
        if self._stream.closed:
            raise BrokenPipeError
        if len(self._pending) + len(data) > self._limit:
            raise ToolError("TOOLKIT_MESSAGE_INVALID")
        self._pending.extend(data)

    async def drain(self):
        data = bytes(self._pending)
        self._pending.clear()

        def write():
            remaining = memoryview(data)
            while remaining:
                written = self._stream.write(remaining)
                if not written:
                    raise BrokenPipeError
                remaining = remaining[written:]
            self._stream.flush()

        await asyncio.to_thread(write)

    def close(self):
        self._pending.clear()
        # stdin is unbuffered. Normal shutdown holds the gateway lock after its
        # last drain; a failed exchange kills the child before closing this pipe.
        self._stream.close()


class _ThreadReader:
    def __init__(self, stream, limit):
        self._stream = stream
        self._limit = limit

    async def readline(self):
        # A missing newline cannot grow memory or wait for arbitrary output.
        return await asyncio.to_thread(self._stream.readline, self._limit)


class _ThreadProcess:
    """Popen adapter for psycopg's Windows SelectorEventLoop.

    Windows selector loops cannot create asyncio subprocess transports. The
    persistent IPC stays unchanged, but blocking pipe and wait calls run in
    threads. Killing the child unblocks those calls after a cancelled exchange.
    """

    def __init__(self, process, limit):
        self._process = process
        self.stdin = _ThreadWriter(process.stdin, limit)
        self.stdout = _ThreadReader(io.BufferedReader(process.stdout), limit)
        self._wait_task = None

    @property
    def returncode(self):
        return self._process.poll()

    def kill(self):
        self._process.kill()

    async def wait(self):
        if self._wait_task is None:
            def wait_and_close():
                try:
                    return self._process.wait()
                finally:
                    # A reader may still be unwinding after cancellation. Close
                    # its pipe in this thread, never while holding the loop.
                    for stream in (self._process.stdin, self.stdout._stream):
                        with contextlib.suppress(OSError):
                            stream.close()

            self._wait_task = asyncio.create_task(asyncio.to_thread(wait_and_close))
        # A gateway timeout can cancel wait() before kill() and another wait().
        # Retain one reaper instead of accumulating blocked pool threads.
        return await asyncio.shield(self._wait_task)


async def _create_subprocess(*argv, **options):
    if os.name != "nt":
        return await asyncio.create_subprocess_exec(*argv, **options)
    limit = options.pop("limit", MAX_MESSAGE_BYTES + 1)
    # Popen does not understand asyncio's stream limit. Use unbuffered stdin so
    # an event-loop close cannot wait for a background writer's buffer lock.
    state = {"cancelled": False, "process": None}
    state_lock = threading.Lock()

    def launch():
        process = subprocess.Popen(
            argv, **options, bufsize=0, creationflags=subprocess.CREATE_NO_WINDOW, shell=False
        )
        with state_lock:
            state["process"] = process
            cancelled = state["cancelled"]
        if cancelled:
            # The async caller may already be gone. This worker still owns the
            # newly created process and reaps it even during loop shutdown.
            with contextlib.suppress(OSError):
                process.kill()
            process.wait()
            for stream in (process.stdin, process.stdout):
                with contextlib.suppress(OSError):
                    stream.close()
        return process

    try:
        process = await asyncio.to_thread(launch)
    except asyncio.CancelledError:
        # Cancellation during process creation must not abandon a newly started
        # child whose handle has not yet been handed to the gateway.
        with state_lock:
            state["cancelled"] = True
            process = state["process"]
        if process is not None:
            adapter = _ThreadProcess(process, limit)
            with contextlib.suppress(OSError):
                adapter.kill()
            await adapter.wait()
        raise
    return _ThreadProcess(process, limit)


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
        self._spawn = process_factory or _create_subprocess
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
