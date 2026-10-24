"""Run one check with its prompts in a log file and its answers read from an inbox file.

The owner does only the browser steps; an assistant watches the log, relays each instruction,
and appends the answer (an empty line means Enter) to the inbox. Both files live under
recordings/sandbox/raw/relay/ (gitignored) and hold only redacted output.

Run: python -m scripts.sandbox_checks.relay D2 [--date YYYY-MM-DD]
"""

import argparse
import asyncio
import sys
import time
from datetime import date
from pathlib import Path

from .checks import CHECK_MODULES, CheckUnavailable
from .env import SettingsError, load_settings
from .manual import ManualSteps
from .redact import Redactor
from .runner import CheckFailed, Runner

RELAY_DIR = Path("recordings/sandbox/raw/relay")
POLL_SECONDS = 2.0


class Relay:
    def __init__(self, check_id: str, directory: Path = RELAY_DIR):
        directory.mkdir(parents=True, exist_ok=True)
        self.log_path = directory / f"{check_id}.log"
        self.inbox_path = directory / f"{check_id}.inbox"
        self.inbox_path.touch()
        # Answers already in the inbox belong to an earlier run.
        self.consumed = len(self.inbox_path.read_text(encoding="utf-8").splitlines())

    def output(self, line: str):
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def input(self, _prompt: str) -> str:
        self.output(f"WAITING for answer #{self.consumed + 1}")
        while True:
            lines = self.inbox_path.read_text(encoding="utf-8").splitlines()
            if len(lines) > self.consumed:
                answer = lines[self.consumed]
                self.consumed += 1
                self.output("ANSWERED")
                return answer
            time.sleep(POLL_SECONDS)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("check_id", type=str.upper, choices=tuple(CHECK_MODULES))
    parser.add_argument("--date", type=date.fromisoformat, help="resume a UTC run date")
    args = parser.parse_args(argv)
    relay = Relay(args.check_id)
    relay.output(f"STARTED {args.check_id}")
    try:
        settings = load_settings()
    except SettingsError as error:
        relay.output(f"STOPPED {error}")
        return 2
    manual = ManualSteps(
        Redactor.from_settings(settings), input_fn=relay.input, output_fn=relay.output
    )
    try:
        state = asyncio.run(
            Runner(settings, output=relay.output, manual=manual).run(
                args.check_id, run_date=args.date
            )
        )
    except (CheckFailed, CheckUnavailable) as error:
        relay.output(f"STOPPED {error}")
        return 1
    except Exception as error:  # noqa: BLE001 - Only the type name may leave this boundary.
        relay.output(f"STOPPED ({type(error).__name__})")
        return 1
    relay.output(f"FINISHED verdict {state['data'].get('verdict')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
