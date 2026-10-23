"""Run a sandbox check: python -m scripts.sandbox_checks run D1."""

import argparse
import asyncio
import sys
from datetime import date

from .checks import CHECK_MODULES, CheckUnavailable
from .env import SettingsError, load_settings
from .runner import CheckFailed, Runner


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="run or resume one check")
    run.add_argument("check_id", type=str.upper, choices=tuple(CHECK_MODULES))
    run.add_argument("--recordings-root", default="recordings/sandbox")
    run.add_argument("--date", type=date.fromisoformat, help="resume a UTC run date (YYYY-MM-DD)")
    run.add_argument(
        "--fresh", action="store_true", help="restart steps; keep all existing evidence"
    )
    args = parser.parse_args(argv)
    try:
        settings = load_settings()
    except SettingsError as error:
        print(str(error), file=sys.stderr)
        return 2
    except Exception as error:  # noqa: BLE001 - CLI output must never expose private exception text.
        print(f"Configuration loading failed ({type(error).__name__}).", file=sys.stderr)
        return 2
    try:
        result = asyncio.run(
            Runner(settings, root=args.recordings_root).run(
                args.check_id, fresh=args.fresh, run_date=args.date
            )
        )
    except (CheckFailed, CheckUnavailable) as error:
        print(str(error), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted; completed steps remain saved.", file=sys.stderr)
        return 130
    except Exception as error:  # noqa: BLE001 - Last boundary before human-facing output.
        print(f"Sandbox check stopped ({type(error).__name__}).", file=sys.stderr)
        return 1
    return 1 if result["data"].get("verdict") == "BLOCKED" else 0


if __name__ == "__main__":
    sys.exit(main())
