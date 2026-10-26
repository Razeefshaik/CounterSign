"""Explicit agent startup and resource ownership, without application database imports."""

import asyncio
from contextlib import AsyncExitStack

from countersign.agent.commander import Commander
from countersign.agent.models import ChatModelConfig, LangChainModel, ModelError, create_model
from countersign.agent.reads import MoneyGraphQueries, ReadToolError, custom_tools
from countersign.agent.toolkit import ToolkitGateway
from countersign.agent.tools import ToolError, ToolRegistry

_STARTUP_CODES = frozenset(
    {
        "MODEL_DEPENDENCIES_REQUIRED",
        "MODEL_CONFIGURATION_INVALID",
        "MONEY_GRAPH_ROLE_REFUSED",
        "MONEY_GRAPH_UNAVAILABLE",
        "TOOLKIT_DEPENDENCIES_MISSING",
        "TOOLKIT_VERSION_UNSUPPORTED",
        "TOOLKIT_SET_MISMATCH",
        "TOOLKIT_CONFIG_INVALID",
        "TOOL_SET_MISMATCH",
    }
)
# One real-model reply may take up to 90 s; a step holds several tool rounds.
REAL_MODEL_STEP_TIMEOUT = 300


class AgentSessionError(RuntimeError):
    """Fixed startup failures; no credential-bearing subprocess or driver text."""


class AgentSession:
    """Create a commander only after its exact read capabilities pass startup.

    A supplied ToolRegistry is an explicitly trusted dependency, useful for
    offline scripted tests. The normal runtime always uses the genuine toolkit
    child and verifies the restricted PostgreSQL role before running a model.
    No provider or PayPal request is made by constructing this object.
    """

    def __init__(
        self, config, paypal, reader, database_url, schema="public", *, tools=None, simulator=None
    ):
        self._model = create_model(config)
        self._owns_model = isinstance(config, ChatModelConfig)
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
                if isinstance(self._model, LangChainModel):
                    await self._model.prepare()
                    if self._owns_model:
                        self._stack.push_async_callback(self._model.aclose)
                tools = self._tools
                if tools is None:
                    graph = MoneyGraphQueries(self._database_url, self._schema)
                    await graph.start()
                    gateway = await self._stack.enter_async_context(ToolkitGateway(self._paypal))
                    tools = ToolRegistry(
                        (*gateway.read_tools(), *custom_tools(self._reader, graph, self._simulator))
                    )
                self._commander = Commander(
                    self._model,
                    tools,
                    timeout=REAL_MODEL_STEP_TIMEOUT if isinstance(self._model, LangChainModel) else 60,
                    secrets=(
                        self._paypal.client_id,
                        self._paypal.client_secret,
                        self._paypal.webhook_id,
                    ),
                )
                return self
            except Exception as exc:  # noqa: BLE001 - dependency errors can contain private values.
                code = str(exc) if isinstance(exc, (ModelError, ReadToolError, ToolError)) else ""
                self._closed = True
                try:
                    await self._stack.aclose()
                finally:
                    raise AgentSessionError(
                        code if code in _STARTUP_CODES else "AGENT_STARTUP_FAILED"
                    ) from None

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
