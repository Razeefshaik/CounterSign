"""C48 stdio entry point. Reads process configuration only; stdout is MCP only."""

import asyncio
import sys

from countersign.config import load_settings
from countersign.db import Database
from countersign.gateway.server import build_server, check_tools
from countersign.gateway.service import Gateway, gateway_mode


async def serve():
    settings = load_settings()
    mode = gateway_mode()
    async with Database(settings) as database:
        server = build_server(Gateway(database, settings, mode=mode))
        await check_tools(server)
        await server.run_stdio_async()


def main():
    try:
        with asyncio.Runner(
            loop_factory=asyncio.SelectorEventLoop
            if sys.platform == "win32"
            else asyncio.new_event_loop
        ) as runner:
            runner.run(serve())
    except Exception:  # noqa: BLE001 - startup errors must never include credentials or private input.
        print("COUNTERSIGN_GATEWAY_START_FAILED", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
