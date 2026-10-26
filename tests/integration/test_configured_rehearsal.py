"""Configured model wiring reaches real toolkit reads and the shield on a local mock."""

import asyncio
import json
import os
import socket
import threading
import time
from contextlib import contextmanager
from dataclasses import replace

import httpx
import pytest
import uvicorn

from countersign.agent import models
from countersign.agent.models import ChatModelConfig
from countersign.agent.tools import ALLOWED_TOOLS
from countersign.config import PayPalSettings
from countersign.db import Database, Repositories
from countersign.harness import Engine, EngineSettings
from countersign.paypal.http import PayPalHTTP
from mock_paypal.app import create_app
from scripts.demo_week import MONDAY, initial_state, mock_order_ids, mock_seed


@contextmanager
def local_mock(app):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    server = uvicorn.Server(uvicorn.Config(app, log_level="critical", access_log=False))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started:
            assert thread.is_alive() and time.monotonic() < deadline
            time.sleep(0.01)
        yield "http://127.0.0.1:" + str(sock.getsockname()[1])
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
        assert not thread.is_alive()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_configured_engine_reads_order_and_dispute_then_executes_stored_evidence(
    db_schema, monkeypatch, caplog
):
    executable = os.environ.get("COUNTERSIGN_TEST_TOOLKIT_PYTHON")
    if not executable:
        pytest.skip("set COUNTERSIGN_TEST_TOOLKIT_PYTHON to the isolated pinned adapter")
    messages = pytest.importorskip("langchain_core.messages")
    monkeypatch.setenv("COUNTERSIGN_TOOLKIT_PYTHON", executable)
    app = create_app(control_enabled=True)
    observed = []

    @app.middleware("http")
    async def observe(request, call_next):
        response = await call_next(request)
        observed.append((request.method, request.url.path, response.status_code))
        return response

    emails = {key: "maker-" + key.lower() + "@example.test" for key in ("A", "B", "C", "C_OLD")}
    orders = mock_order_ids()
    capture = orders["A400"]["capture_id"]
    order_id = orders["A400"]["order_id"]
    with local_mock(app) as origin:
        async with (
            Database(db_schema.conninfo) as database,
            httpx.AsyncClient(base_url=origin, trust_env=False) as control,
            PayPalHTTP(PayPalSettings(origin), monotonic=lambda: MONDAY.timestamp()) as http,
        ):
            assert (
                await control.post("/_mock/clock/set", json={"now": MONDAY.isoformat()})
            ).status_code == 200
            provider_state = mock_seed(emails, orders, "UNUSED-PRIOR")
            provider_state["payout_items"] = []
            assert (await control.post("/_mock/seed", json=provider_state)).status_code == 200
            settings = EngineSettings(
                db_schema.conninfo,
                origin,
                "mock-client",
                "mock-secret",
                "",
                lambda: MONDAY,
            )
            async with Engine(settings, database=database, http=http) as setup:
                state = initial_state(emails, orders, "UNUSED-PRIOR")
                state["orders"] = [order for order in state["orders"] if order["key"] == "A400"]
                state["prior_payouts"] = []
                await setup.seed(state)
                response = await control.post(
                    "/_mock/disputes",
                    json={
                        "capture_id": capture,
                        "amount": "400.00",
                        "reason": "MERCHANDISE_OR_SERVICE_NOT_RECEIVED",
                    },
                )
                assert response.status_code == 200
                dispute_id = response.json()["dispute_id"]
                for _ in range(50):
                    assert (await setup.tick())["errors"] == 0
                    async with database.tx() as conn:
                        incidents = await Repositories(conn).incidents.find({"kind": "DISPUTE"})
                    if incidents:
                        break
                    await asyncio.sleep(0.01)
                assert len(incidents) == 1

            class FakeConfiguredChat:
                def __init__(self):
                    self.turns = 0

                def bind_tools(self, tools):
                    assert {tool["function"]["name"] for tool in tools} == ALLOWED_TOOLS
                    return self

                async def ainvoke(self, turns, config):
                    self.turns += 1
                    assert config == {"callbacks": []}
                    if self.turns == 1:
                        return messages.AIMessage(
                            content="",
                            tool_calls=[
                                {
                                    "id": "read-order",
                                    "name": "get_order_details",
                                    "args": {"order_id": order_id},
                                },
                                {
                                    "id": "read-dispute",
                                    "name": "get_dispute",
                                    "args": {"dispute_id": dispute_id},
                                },
                            ],
                        )
                    assert self.turns == 2
                    assert isinstance(turns[-1], messages.ToolMessage)
                    assert isinstance(turns[-2], messages.ToolMessage)
                    observed_order = json.loads(turns[-2].content)["untrusted_result"]
                    observed_dispute = json.loads(turns[-1].content)["untrusted_result"]
                    assert observed_order["raw"]["id"] == order_id
                    assert observed_dispute["dispute_id"] == dispute_id
                    return messages.AIMessage(
                        content=json.dumps(
                            {
                                "incident_id": str(incidents[0]["id"]),
                                "action": "PROVIDE_EVIDENCE",
                                "params": {"paypal_dispute_id": dispute_id},
                                "evidence_refs": [
                                    "capture:" + capture,
                                    "tracking:9400100000000000000000",
                                ],
                                "rationale": "Submit the stored tracking permitted by the observed dispute links.",
                                "expected_effect": {
                                    "available_change": {"minor": 0, "currency": "USD"}
                                },
                            }
                        )
                    )

            fake_chat = FakeConfiguredChat()
            monkeypatch.setattr(models, "_langchain_factory", lambda _: fake_chat)
            configured = replace(
                settings,
                llm=ChatModelConfig(
                    "google_genai",
                    "offline-configured-rehearsal",
                    "FAKE-LLM-KEY",
                ),
            )
            # No injected read registry: startup checks the actual restricted
            # database role and starts the actual isolated toolkit child.
            async with Engine(configured, database=database, http=http) as engine:
                result = await engine.tick()
                assert result["errors"] == 0 and result["operations"] == 1
            assert fake_chat.turns == 2
            async with database.tx() as conn:
                repos = Repositories(conn)
                [proposal] = await repos.proposals.find()
                assert proposal["model"] == "offline-configured-rehearsal"
                assert proposal["action"] == "PROVIDE_EVIDENCE"
                assert proposal["state"] == "EXECUTED" and proposal["verdict"] == "ALLOW"
                [operation] = await repos.operations.find()
                assert operation["kind"] == "DISPUTE_EVIDENCE" and operation["state"] == "SUCCEEDED"
                assert await repos.audit_log.find(
                    {"kind": "SHIELD_VERDICT", "proposal_id": proposal["id"]}
                )
            assert "FAKE-LLM-KEY" not in caplog.text
            assert "TOOLKIT_CALL_FAILED" not in caplog.text
    assert any(path == "/v2/checkout/orders/" + order_id for _, path, _ in observed)
    assert any(path == "/v1/customer/disputes/" + dispute_id for _, path, _ in observed)
    writes = [
        path for method, path, _ in observed if method == "POST" and not path.startswith("/_mock/")
    ]
    assert all(
        path in {"/v1/oauth2/token", "/v1/customer/disputes/" + dispute_id + "/provide-evidence"}
        for path in writes
    )
    assert sum(path.endswith("/provide-evidence") for path in writes) == 1
