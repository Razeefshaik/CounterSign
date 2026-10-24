"""Agent startup is explicit, fails closed and owns only its opened resources."""

from types import SimpleNamespace

import pytest

from countersign.agent import session
from countersign.agent.models import ChatModelConfig, ModelError, ScriptedModel
from countersign.agent.session import AgentSession, AgentSessionError
from countersign.agent.tools import (
    ALLOWED_TOOLS,
    CUSTOM_TOOLS,
    TOOLKIT_TOOLS,
    ReadTool,
    ToolRegistry,
)
from countersign.config import PayPalSettings


async def empty(arguments):
    return {"items": []}


def tools(names=ALLOWED_TOOLS):
    return tuple(ReadTool(name, "Read-only fixture", {"type": "object"}, empty) for name in names)


def proposal():
    return {
        "incident_id": "0192f1c4-7a10-7c3e-9b1a-5d2f00000042",
        "action": "WAIT",
        "params": {},
        "evidence_refs": [],
        "rationale": "Await the next confirmed observation",
        "expected_effect": {"available_change": {"minor": 0, "currency": "USD"}},
    }


def configured(model=None, **kwargs):
    return AgentSession(
        model if model is not None else ScriptedModel([proposal()]),
        PayPalSettings(), object(), "postgresql://private:private-secret@localhost/demo_eval",
        **kwargs,
    )


def test_missing_real_model_or_key_fails_before_resource_startup():
    with pytest.raises(ModelError, match="MODEL_PROVIDER_AND_ID_REQUIRED"):
        configured(ChatModelConfig())
    with pytest.raises(ModelError, match="MODEL_API_KEY_REQUIRED"):
        configured(ChatModelConfig("chosen-provider", "chosen-model"))


def test_injected_tools_must_be_the_exact_registry():
    with pytest.raises(AgentSessionError, match="AGENT_TOOL_REGISTRY_REQUIRED"):
        configured(tools=object())


@pytest.mark.asyncio
async def test_explicit_scripted_session_never_opens_toolkit_or_database(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("external resource opened by offline scripted test")

    monkeypatch.setattr(session, "ToolkitGateway", forbidden)
    monkeypatch.setattr(session, "MoneyGraphQueries", forbidden)
    model = ScriptedModel([proposal()])
    agent = configured(model, tools=ToolRegistry(tools()))
    assert model.requests == []
    assert "private-secret" not in repr(agent)
    with pytest.raises(AgentSessionError, match="AGENT_SESSION_NOT_OPEN"):
        await agent.step({"id": proposal()["incident_id"]})
    async with agent:
        assert agent.model_id == "scripted"
        result = await agent.step({"id": proposal()["incident_id"]})
        assert result.to_dict() == proposal()
    with pytest.raises(AgentSessionError, match="AGENT_SESSION_NOT_OPEN"):
        await agent.step({"id": proposal()["incident_id"]})
    with pytest.raises(AgentSessionError, match="AGENT_SESSION_CLOSED"):
        await agent.__aenter__()
    await agent.aclose()


def dependencies(monkeypatch, *, bad_role=False, missing_tool=False):
    observed = SimpleNamespace(graph_starts=0, gateway_opens=0, gateway_closes=0, schema=None)

    class Graph:
        def __init__(self, database_url, schema):
            observed.schema = schema

        async def start(self):
            observed.graph_starts += 1
            if bad_role:
                raise RuntimeError("driver included private-secret")

    class Gateway:
        def __init__(self, paypal):
            pass

        async def __aenter__(self):
            observed.gateway_opens += 1
            return self

        async def __aexit__(self, *args):
            observed.gateway_closes += 1

        def read_tools(self):
            selected = TOOLKIT_TOOLS - {"get_dispute"} if missing_tool else TOOLKIT_TOOLS
            return tools(selected)

    monkeypatch.setattr(session, "MoneyGraphQueries", Graph)
    monkeypatch.setattr(session, "ToolkitGateway", Gateway)
    monkeypatch.setattr(session, "custom_tools", lambda *args: tools(CUSTOM_TOOLS))
    return observed


@pytest.mark.asyncio
async def test_normal_startup_checks_role_and_exact_set_before_any_model_call(monkeypatch):
    observed = dependencies(monkeypatch)
    model = ScriptedModel([proposal()])
    async with configured(model, schema="isolated_test_schema") as agent:
        assert observed.schema == "isolated_test_schema"
        assert (observed.graph_starts, observed.gateway_opens) == (1, 1)
        assert model.requests == []
        assert await agent.__aenter__() is agent
        assert observed.gateway_opens == 1
        assert (await agent.step({"id": proposal()["incident_id"]})).action == "WAIT"
    assert observed.gateway_closes == 1


@pytest.mark.asyncio
async def test_unsafe_database_role_fails_before_child_or_model(monkeypatch):
    observed = dependencies(monkeypatch, bad_role=True)
    model = ScriptedModel([proposal()])
    agent = configured(model)
    with pytest.raises(AgentSessionError, match="AGENT_STARTUP_FAILED") as error:
        await agent.__aenter__()
    assert "private-secret" not in str(error.value)
    assert observed.gateway_opens == 0
    assert model.requests == []


@pytest.mark.asyncio
async def test_tool_drift_closes_child_without_calling_model(monkeypatch):
    observed = dependencies(monkeypatch, missing_tool=True)
    model = ScriptedModel([proposal()])
    with pytest.raises(AgentSessionError, match="AGENT_STARTUP_FAILED"):
        async with configured(model):
            pytest.fail("unsafe agent session opened")
    assert (observed.gateway_opens, observed.gateway_closes) == (1, 1)
    assert model.requests == []
