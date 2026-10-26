"""Unavailable evidence is auditable but never becomes an executable request."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio

from countersign.agent.models import ScriptedModel
from countersign.agent.tools import ALLOWED_TOOLS, ReadTool, ToolRegistry
from countersign.approvals import ApprovalError, load_context
from countersign.approvals import service as approval_module
from countersign.config import PayPalSettings
from countersign.db import Database, Repositories
from countersign.harness import Engine, EngineSettings
from countersign.paypal.http import PayPalHTTP
from countersign.shield.engine import Verdict
from mock_paypal.app import create_app

NOW = datetime(2026, 11, 2, 9, tzinfo=UTC)
BASE = "http://localhost:8099"
pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def read_tools():
    async def unused(_):
        pytest.fail("the scripted evidence proposal does not invoke tools")

    return ToolRegistry(
        ReadTool(
            name,
            "Unused read-only test capability",
            {"type": "object", "properties": {}, "additionalProperties": False},
            unused,
        )
        for name in sorted(ALLOWED_TOOLS)
    )


@pytest_asyncio.fixture
async def unavailable_evidence(db_schema):
    mock = create_app(control_enabled=True)
    transport = httpx.ASGITransport(app=mock)
    provider_requests = []

    class Observe(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            provider_requests.append((request.method, request.url.path))
            return await transport.handle_async_request(request)

    async with (
        Database(db_schema.conninfo) as database,
        httpx.AsyncClient(transport=transport, base_url=BASE) as control,
        PayPalHTTP(PayPalSettings(BASE), transport=Observe()) as http,
    ):
        assert (
            await control.post(
                "/_mock/seed",
                json={
                    "balances": {"available": "100.00", "withheld": "0.00", "currency_code": "USD"},
                    "captures": [{"id": "CAPTURE", "amount": "10.00"}],
                },
            )
        ).status_code == 200
        settings = EngineSettings(
            db_schema.conninfo, BASE, "mock-client", "mock-secret", "", lambda: NOW
        )
        async with Engine(settings, database=database, http=http) as engine:
            await engine.seed(
                {
                    "makers": [
                        {
                            "key": "A",
                            "display_name": "Maker A",
                            "payout_email": "maker@example.test",
                            "verified": True,
                        }
                    ],
                    "orders": [
                        {
                            "key": "order",
                            "maker": "A",
                            "amount": {"minor": 1000, "currency": "USD"},
                            "capture_id": "CAPTURE",
                            "tracking": {"carrier": "USPS", "tracking_number": "TRACK-123"},
                        }
                    ],
                }
            )
            created = await control.post(
                "/_mock/disputes",
                json={
                    "capture_id": "CAPTURE",
                    "amount": "10.00",
                    "reason": "MERCHANDISE_OR_SERVICE_NOT_RECEIVED",
                },
            )
            assert created.status_code == 200
            external_id = created.json()["dispute_id"]
            transitioned = await control.post(
                f"/_mock/disputes/{external_id}/transition",
                json={"status": "UNDER_REVIEW", "stage": "CHARGEBACK"},
            )
            assert transitioned.status_code == 200
            # A sibling isolated schema can hold the reconciler's global lease;
            # an error-free tick may legitimately skip polling. Await the fact.
            for _ in range(50):
                assert (await engine.tick())["errors"] == 0
                async with database.tx() as conn:
                    observed = await Repositories(conn).disputes.find()
                if observed:
                    break
                await asyncio.sleep(0.01)
            assert observed, "the staged dispute must be reconciled before proposal tests"
            async with database.tx() as conn:
                repos = Repositories(conn)
                [dispute] = await repos.disputes.find()
                [incident] = await repos.incidents.find({"kind": "DISPUTE"})
                assert dispute["status"] == "UNDER_REVIEW"
                assert all(link.get("rel") != "provide_evidence" for link in dispute["links"])
            yield SimpleNamespace(
                database=database,
                settings=settings,
                http=http,
                requests=provider_requests,
                dispute=dispute,
                incident=incident,
            )


async def created_proposal(world):
    async with world.database.tx() as conn:
        return await Repositories(conn).proposals.insert(
            action="PROVIDE_EVIDENCE",
            params={"paypal_dispute_id": world.dispute["paypal_dispute_id"]},
            evidence_refs=["tracking:TRACK-123"],
            model="scripted",
            raw_output={},
            state="CREATED",
        )


async def test_commander_records_a_real_shield_block_without_an_operation(unavailable_evidence):
    world = unavailable_evidence
    model = ScriptedModel(
        [
            {
                "incident_id": str(world.incident["id"]),
                "action": "PROVIDE_EVIDENCE",
                "params": {"paypal_dispute_id": world.dispute["paypal_dispute_id"]},
                "evidence_refs": ["tracking:TRACK-123"],
                "rationale": "Submit the stored tracking if the dispute permits evidence.",
                "expected_effect": {"available_change": {"minor": 0, "currency": "USD"}},
            }
        ]
    )
    async with Engine(
        replace(world.settings, llm=model),
        database=world.database,
        http=world.http,
        agent_tools=read_tools(),
    ) as engine:
        result = await engine.tick()
    assert result["errors"] == 0 and result["operations"] == 0
    assert len(model.requests) == 1
    async with world.database.tx() as conn:
        repos = Repositories(conn)
        [proposal] = await repos.proposals.find()
        assert proposal["state"] == "BLOCKED" and proposal["verdict"] == "BLOCK"
        rules = {row["rule"]: row["outcome"] for row in proposal["verdict_details"]["rule_results"]}
        assert rules["EVIDENCE_INTEGRITY"] == "BLOCK"
        assert "request" not in proposal["verdict_details"]["operation_payload"]
        [audit] = await repos.audit_log.find(
            {"kind": "SHIELD_VERDICT", "proposal_id": proposal["id"]}
        )
        assert audit["verdict"] == "BLOCK"
        assert await repos.operations.find() == []
        assert await repos.approvals.find() == []
    assert all(method == "GET" or path == "/v1/oauth2/token" for method, path in world.requests)


@pytest.mark.parametrize("state", ["ALLOWED", "APPROVED", "EXECUTING"])
async def test_ready_evidence_still_requires_a_strict_executable_request(
    unavailable_evidence, state
):
    world = unavailable_evidence
    proposal = await created_proposal(world)
    # load_context receives the executor's locked row; changing this copy tests
    # its boundary without bypassing the database's legal transition trigger.
    async with world.database.tx() as conn:
        with pytest.raises(ApprovalError, match="ACTION_PRECONDITION_FAILED"):
            await load_context(conn, {**proposal, "state": state}, now=NOW)
        assert await Repositories(conn).operations.find() == []


@pytest.mark.parametrize("verdict", [Verdict.ALLOW, Verdict.NEEDS_OWNER])
async def test_missing_request_cannot_escape_if_other_rules_stop_blocking(
    unavailable_evidence, monkeypatch, verdict
):
    world = unavailable_evidence
    proposal = await created_proposal(world)
    monkeypatch.setattr(approval_module, "evaluate", lambda *_: SimpleNamespace(verdict=verdict))
    async with world.database.tx() as conn:
        with pytest.raises(ApprovalError, match="ACTION_PRECONDITION_FAILED"):
            await load_context(conn, proposal, now=NOW)
        assert await Repositories(conn).operations.find() == []
