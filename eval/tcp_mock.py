"""Serve one in-process mock app on an ephemeral loopback TCP port (M13).

The server runs as a task on the caller's event loop, so the runner keeps
direct access to the app (clock, webhook deliveries) while another process,
such as CounterSign's genuine toolkit child, can reach it over real HTTP.
It binds 127.0.0.1 port 0 only: never a public interface, never a fixed port.
uvicorn's logging is silenced so injected-fault tracebacks never reach output.
"""

import asyncio
import contextlib
import socket
from contextlib import asynccontextmanager


@asynccontextmanager
async def serve_loopback(app_factory, *, startup_timeout=10.0):
    """Yield (app, base_url); app_factory(base_url) builds the app to serve."""
    import uvicorn

    class _QuietServer(uvicorn.Server):
        # Never replace the caller's signal handlers (pytest, CLI Ctrl+C).
        @contextlib.contextmanager
        def capture_signals(self):
            yield

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        sock.listen(64)
        host, port = sock.getsockname()[:2]
        if host != "127.0.0.1":
            raise RuntimeError("Loopback bind failed")
        base_url = f"http://127.0.0.1:{port}"
        app = app_factory(base_url)
        config = uvicorn.Config(
            app,
            lifespan="off",
            log_config=None,
            log_level="critical",
            access_log=False,
            timeout_graceful_shutdown=5,
        )
        server = _QuietServer(config)
        task = asyncio.create_task(server.serve(sockets=[sock]))
        try:
            async with asyncio.timeout(startup_timeout):
                while not server.started:
                    if task.done():
                        raise RuntimeError("Loopback mock failed to start")
                    await asyncio.sleep(0.01)
            yield app, base_url
        finally:
            server.should_exit = True
            # Shutdown noise must not mask the run's own result or exception.
            with contextlib.suppress(Exception):
                await task
    finally:
        sock.close()
