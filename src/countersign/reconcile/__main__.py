"""Run reconciliation on a schedule, or print safe counts for one cycle."""

import argparse
import asyncio
import json
import sys
from dataclasses import asdict

from countersign.config import load_settings
from countersign.db import Database
from countersign.paypal.http import PayPalHTTP
from countersign.paypal.read import PayPalRead

from .poller import Reconciler, ReconcileSettings


async def _run(args):
    runtime = load_settings()
    async with Database(runtime) as database, PayPalHTTP(runtime.paypal) as http:
        service = Reconciler(
            database,
            PayPalRead(http),
            settings=ReconcileSettings(
                interval_seconds=args.interval, backfill_enabled=args.backfill
            ),
        )
        if args.once:
            print(json.dumps(asdict(await service.poll_once())))
        else:
            await service.run(asyncio.Event())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Run one cycle and print counts")
    parser.add_argument("--backfill", action="store_true", help="Opt in to pending-D11 event reads")
    parser.add_argument("--interval", type=float, default=60, help="Seconds between cycles")
    args = parser.parse_args()
    try:
        loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
        with asyncio.Runner(loop_factory=loop_factory) as runner:
            runner.run(_run(args))
    except KeyboardInterrupt:
        return
    except Exception:  # noqa: BLE001 - command failures never expose credentials or driver data.
        raise SystemExit("Reconciliation failed; inspect safe incident records") from None


if __name__ == "__main__":
    main()
