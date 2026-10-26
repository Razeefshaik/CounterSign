"""The genuine isolated adapter reads the actual local mock and a strict HTTP stub."""

import asyncio
import os
import re
import socket
import sys
import threading
import time
from contextlib import contextmanager
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import uvicorn
from fastapi import FastAPI, Request

from countersign.agent.commander import Commander
from countersign.agent.toolkit import ToolkitGateway
from countersign.agent.tools import CUSTOM_TOOLS, TOOLKIT_TOOLS, ReadTool, ToolRegistry
from countersign.config import PayPalSettings
from mock_paypal.app import create_app
from scripts.demo_week import MONDAY, mock_order_ids, mock_seed

EMAILS = {key: "maker-" + key.lower() + "@example.test" for key in ("A", "B", "C", "C_OLD")}


@pytest.fixture
def toolkit_python():
    executable = os.environ.get("COUNTERSIGN_TEST_TOOLKIT_PYTHON")
    if not executable:
        pytest.skip(
            "set COUNTERSIGN_TEST_TOOLKIT_PYTHON to the pinned isolated adapter interpreter"
        )
    return executable


@contextmanager
def http_server(app):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    origin = "http://127.0.0.1:" + str(sock.getsockname()[1])
    server = uvicorn.Server(uvicorn.Config(app, log_level="critical", access_log=False))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started:
            assert thread.is_alive() and time.monotonic() < deadline, (
                "local test server did not start"
            )
            time.sleep(0.01)
        yield origin
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
        assert not thread.is_alive()


def run(coroutine):
    with asyncio.Runner(
        loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None
    ) as runner:
        return runner.run(coroutine)


def proposal():
    return {
        "incident_id": "rehearsal-incident",
        "action": "WAIT",
        "params": {},
        "evidence_refs": [],
        "rationale": "The mock reads completed; await a decision.",
        "expected_effect": {"available_change": {"minor": 0, "currency": "USD"}},
    }


def test_new_mock_ids_satisfy_the_pinned_order_and_transaction_schemas():
    first, second = mock_order_ids(), mock_order_ids()
    values = [
        value for group in (first, second) for row in group.values() for value in row.values()
    ]
    assert len(set(values)) == 16
    assert all(re.fullmatch(r"[A-Z0-9]{17,32}", value) for value in values)


def test_real_commander_and_child_complete_all_five_reads_on_actual_mock(toolkit_python):
    app = create_app(control_enabled=True)
    observed = []

    @app.middleware("http")
    async def observe(request, call_next):
        response = await call_next(request)
        observed.append((request.method, request.url.path, response.status_code))
        return response

    orders = mock_order_ids()
    oid, capture = orders["A400"]["order_id"], orders["A400"]["capture_id"]
    with http_server(app) as origin:

        async def scenario():
            async with httpx.AsyncClient(base_url=origin, trust_env=False) as control:
                assert (
                    await control.post("/_mock/clock/set", json={"now": MONDAY.isoformat()})
                ).status_code == 200
                assert (
                    await control.post("/_mock/seed", json=mock_seed(EMAILS, orders, "OLD-ITEM"))
                ).status_code == 200
                created = await control.post(
                    "/_mock/disputes",
                    json={
                        "capture_id": capture,
                        "amount": "400.00",
                        "reason": "MERCHANDISE_OR_SERVICE_NOT_RECEIVED",
                    },
                )
                assert created.status_code == 200
                dispute_id = created.json()["dispute_id"]
            async with ToolkitGateway(
                PayPalSettings(origin), python_executable=toolkit_python
            ) as gateway:
                tools = {tool.name: tool for tool in gateway.read_tools()}
                assert tools.keys() == TOOLKIT_TOOLS
                pattern = tools["get_order_details"].schema["properties"]["order_id"]["pattern"]
                assert re.fullmatch(pattern, oid)
                assert not re.fullmatch(pattern, "DEMO-O-A400-" + "0" * 36)

                async def unused(_):
                    pytest.fail("only genuine toolkit reads are required")

                registry = ToolRegistry(
                    [
                        *tools.values(),
                        *[
                            ReadTool(name, "unused", {"type": "object"}, unused)
                            for name in CUSTOM_TOOLS
                        ],
                    ]
                )

                class FakeChat:
                    model_id = "offline-rehearsal"

                    def __init__(self):
                        self.turns = 0

                    async def complete(self, request):
                        self.turns += 1
                        if self.turns == 1:
                            calls = [
                                ("get_dispute", {"dispute_id": dispute_id}),
                                ("list_disputes", {}),
                                ("get_shipment_tracking", {"transaction_id": capture}),
                                ("get_order_details", {"order_id": oid}),
                            ]
                        elif self.turns == 2:
                            observations = request["observations"]
                            assert observations[0]["untrusted_result"]["dispute_id"] == dispute_id
                            assert observations[2]["untrusted_result"]["trackers"]
                            assert observations[3]["untrusted_result"]["raw"]["id"] == oid
                            calls = [
                                (
                                    "list_transactions",
                                    {
                                        "start_date": "2026-11-02T00:00:00Z",
                                        "end_date": "2026-11-03T00:00:00Z",
                                    },
                                ),
                                ("get_shipment_tracking", {"order_id": oid}),
                            ]
                        else:
                            assert self.turns == 3
                            # D12: an empty search omits transaction_details entirely,
                            # and reporting lag may honestly leave the result empty.
                            assert isinstance(
                                request["observations"][4]["untrusted_result"].get(
                                    "transaction_details", []
                                ),
                                list,
                            )
                            assert request["observations"][5]["untrusted_result"]["trackers"]
                            return proposal()
                        return {
                            "tool_calls": [
                                {"id": f"read-{self.turns}-{index}", "name": name, "args": args}
                                for index, (name, args) in enumerate(calls)
                            ]
                        }

                chat = FakeChat()
                result = await Commander(chat, registry).step({"id": "rehearsal-incident"})
                assert result.action == "WAIT" and chat.turns == 3

        run(scenario())
    reads = [
        (method, path, status)
        for method, path, status in observed
        if not path.startswith("/_mock/")
    ]
    assert all(status == 200 for _, _, status in reads)
    assert all(method == "GET" or path == "/v1/oauth2/token" for method, path, _ in reads)
    assert {path for method, path, _ in reads if method == "GET"} == {
        "/v1/customer/disputes",
        "/v1/customer/disputes/" + dispute_id_from(observed),
        "/v1/shipping/trackers",
        "/v2/checkout/orders/" + oid,
        "/v1/reporting/transactions",
    }


def dispute_id_from(observed):
    return next(
        path.rsplit("/", 1)[1]
        for method, path, _ in observed
        if method == "GET" and path.startswith("/v1/customer/disputes/")
    )


def test_isolated_child_normalizes_transaction_id_search_against_http_stub(toolkit_python):
    app, observed = FastAPI(), []
    transaction_id = "MOCKTRANSACTION12345"

    @app.post("/v1/oauth2/token")
    async def token():
        return {"access_token": "FAKE-TEST-TOKEN", "token_type": "Bearer"}

    @app.get("/v1/reporting/transactions")
    async def transactions(request: Request):
        parsed = parse_qs(urlsplit(str(request.url)).query)
        observed.append(parsed)
        assert parsed["transaction_id"] == [transaction_id]
        assert "search_months" not in parsed
        return {"transaction_details": [{"transaction_info": {"transaction_id": transaction_id}}]}

    with http_server(app) as origin:

        async def scenario():
            async with ToolkitGateway(
                PayPalSettings(origin), python_executable=toolkit_python
            ) as gateway:
                tool = next(
                    tool for tool in gateway.read_tools() if tool.name == "list_transactions"
                )
                result = await tool.handler({"transaction_id": transaction_id, "search_months": 1})
                assert result["found"] is True
                assert (
                    result["transaction_details"][0]["transaction_info"]["transaction_id"]
                    == transaction_id
                )

        run(scenario())
    assert len(observed) == 1
