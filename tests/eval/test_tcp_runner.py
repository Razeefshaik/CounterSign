"""M13 TCP-backed runner option: the same in-process mock on an ephemeral loopback port."""

import asyncio
import json
import socket
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from eval.runner import DummySystem, run_one, run_suite
from eval.systems.rules_only import RulesOnlySystem
from eval.tcp_mock import serve_loopback
from mock_paypal.app import create_app

SCENARIOS = Path(__file__).resolve().parents[2] / "eval/scenarios"


def scenario(name="F01_timeout_after_payout_created"):
    return json.loads((SCENARIOS / (name + ".json")).read_text(encoding="utf-8"))


PROBE = (
    "import sys, urllib.request\n"
    "with urllib.request.urlopen(sys.argv[1] + '/_mock/clock', timeout=10) as r:\n"
    "    print(r.status)\n"
)


class Probe(DummySystem):
    """Checks reachability from a separate process, as the toolkit child needs."""

    def __init__(self, probe=True):
        self.probe, self.status = probe, None

    async def setup(self, env):
        self.base = env.mock_base_url
        if not self.probe:
            return
        result = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-I", "-c", PROBE, env.mock_base_url],
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.status = result.stdout.strip()


@pytest.mark.asyncio
async def test_tcp_mock_is_reachable_from_another_process_and_closed_afterwards():
    system = Probe()
    row = await run_one(scenario(), system, run_id="tcp", k_index=0, mock_transport="tcp")
    assert row["error"] is None
    assert system.status == "200"
    parsed = urlsplit(system.base)
    assert parsed.scheme == "http" and parsed.hostname == "127.0.0.1"
    assert parsed.port not in {8000, 8080, 8099}
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", parsed.port), timeout=1).close()


@pytest.mark.asyncio
async def test_default_stays_in_process_and_unknown_transports_are_refused(tmp_path):
    system = Probe(probe=False)  # never probe host port 8099: other tools may use it
    row = await run_one(scenario(), system, run_id="asgi", k_index=0)
    assert row["error"] is None and system.base == "http://localhost:8099"
    with pytest.raises(ValueError):
        await run_one(scenario(), DummySystem(), run_id="x", k_index=0, mock_transport="udp")
    with pytest.raises(ValueError):
        await run_suite([scenario()], [DummySystem], output=tmp_path, k=1, mock_transport="public")
    assert not list(tmp_path.iterdir())


@pytest.mark.asyncio
async def test_rules_only_results_match_across_transports(tmp_path):
    data = [scenario(), scenario("F_unclaimed_cancel_race")]
    rows = {}
    for transport in ("asgi", "tcp"):
        rows[transport] = await run_suite(
            data,
            [RulesOnlySystem],
            output=tmp_path / transport,
            k=1,
            run_id="same",
            mock_transport=transport,
        )
        for row in rows[transport]:
            row.pop("duration_ms")
    assert rows["asgi"] == rows["tcp"]
    assert [row["passed"] for row in rows["tcp"]] == [True, False]


@pytest.mark.asyncio
async def test_loopback_server_binds_only_127_0_0_1_and_reports_its_url():
    async with serve_loopback(
        lambda base: create_app(control_enabled=True, mock_base_url=base)
    ) as (
        app,
        base,
    ):
        assert base.startswith("http://127.0.0.1:")
        assert app.state.webhook_service is not None
