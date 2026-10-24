"""Explicit agent startup and resource ownership, without application database imports."""

import asyncio
from contextlib import AsyncExitStack

from countersign.agent.commander import Commander
from countersign.agent.models import create_model
from countersign.agent.reads import MoneyGraphQueries, custom_tools
from countersign.agent.toolkit import ToolkitGateway
from countersign.agent.tools import ToolRegistry


class AgentSessionError(RuntimeError):
    """Fixed startup failures; no credential-bearing subprocess or driver text."""


class AgentSession:
    """Create a commander only after its exact read capabilities pass startup.

    A supplied ToolRegistry is an explicitly trusted dependency, useful for
    offline scripted tests. The normal runtime always uses the genuine toolkit
    child and verifies the restricted PostgreSQL role before running a model.
    No provider or PayPal request is made by constructing this object.
    """

    def __init__(self, config, paypal, reader, database_url, schema="public", *,
                 tools=None, simulator=None):
        self._model = create_model(config)
        if tools is not None and not isinstance(tools, ToolRegistry):
            raise AgentSessionError("AGENT_TOOL_REGISTRY_REQUIRED")
        self._paypal, self._reader = paypal, reader
        self._database_url, self._schema = database_url, schema
        self._tools, self._simulator = tools, simulator
        self._stack = AsyncExitStack()
        self._commander = None
        self._closed = False
        self._lock = asyncio.Lock()

    def __repr__(self):
        return "AgentSession(values=<REDACTED>)"

    async def __aenter__(self):
        async with self._lock:
            if self._closed:
                raise AgentSessionError("AGENT_SESSION_CLOSED")
            if self._commander is not None:
                return self
            try:
                tools = self._tools
                if tools is None:
                    graph = MoneyGraphQueries(self._database_url, self._schema)
                    await graph.start()
                    gateway = await self._stack.enter_async_context(ToolkitGateway(self._paypal))
                    tools = ToolRegistry((*gateway.read_tools(),
                                          *custom_tools(self._reader, graph, self._simulator)))
                self._commander = Commander(
                    self._model, tools,
                    secrets=(self._paypal.client_id, self._paypal.client_secret,
                             self._paypal.webhook_id),
                )
                return self
            except Exception:  # noqa: BLE001 - startup errors can contain private connection values.
                await self._stack.aclose()
                self._closed = True
                raise AgentSessionError("AGENT_STARTUP_FAILED") from None

    async def __aexit__(self, *args):
        await self.aclose()

    async def aclose(self):
        async with self._lock:
            self._closed = True
            self._commander = None
            try:
                await self._stack.aclose()
            except Exception:  # noqa: BLE001 - subprocess cleanup errors must remain private.
                raise AgentSessionError("AGENT_CLOSE_FAILED") from None

    @property
    def model_id(self):
        if self._commander is None:
            raise AgentSessionError("AGENT_SESSION_NOT_OPEN")
        return self._commander.model_id

    async def step(self, incident, context=None):
        if self._commander is None or self._closed:
            raise AgentSessionError("AGENT_SESSION_NOT_OPEN")
        return await self._commander.step(incident, context)
