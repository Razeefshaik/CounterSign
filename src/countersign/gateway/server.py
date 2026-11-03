"""Official MCP SDK transports over the exact gateway capability set."""

import os
from typing import Any
from urllib.parse import urlsplit

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp_types import CallToolResult, TextContent, ToolAnnotations
from starlette.responses import RedirectResponse

from countersign.agent.safety import SafetyError, sanitize
from countersign.gateway.service import ALLOWED_TOOLS, GatewayError, identifier, list_actions


async def guarded(call):
    try:
        return await call()
    except GatewayError as error:
        raise ToolError(str(error)) from None
    except Exception:  # noqa: BLE001 - no caller text, DSN, driver or upstream error is public.
        raise ToolError("GATEWAY_UNAVAILABLE") from None


def caller(context):
    params = context.session.client_params
    return (
        params.client_info.name
        if params is not None and params.client_info is not None
        else "unknown"
    )


def build_server(gateway):
    allowed_arguments = {}

    async def input_boundary(context, call_next):
        if context.method == "tools/call":
            params = context.params or {}
            name, arguments = params.get("name"), params.get("arguments", {})
            try:
                if (
                    type(name) is not str
                    or name not in allowed_arguments
                    or type(arguments) is not dict
                    or not arguments.keys() <= allowed_arguments[name]
                ):
                    raise SafetyError("INVALID_REQUEST")
                sanitize(arguments)
            except (SafetyError, RecursionError):
                return CallToolResult(
                    is_error=True,
                    content=[
                        TextContent(
                            type="text",
                            text="INVALID_REQUEST",
                        )
                    ],
                )
        return await call_next(context)

    server = MCPServer(
        "CounterSign",
        version="0.1.0",
        log_level="CRITICAL",
        subscriptions=False,
        instructions="Read marketplace facts and propose actions for deterministic shield judgement. "
        "Money decisions requiring the owner remain in the CounterSign dashboard.",
        middleware=[input_boundary],
    )
    read = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False)
    proposal = ToolAnnotations(
        read_only_hint=gateway.mode == "preview", destructive_hint=False, open_world_hint=False
    )

    # Any + defaults keep untrusted values inside our strict parser and fixed-error
    # boundary, rather than letting SDK argument diagnostics reflect caller data.
    @server.tool(annotations=read)
    async def list_incidents(
        state: Any = None, kind: Any = None, limit: Any = 50
    ) -> dict[str, Any]:
        """Read a bounded list of marketplace incidents, optionally by state and kind."""
        return await guarded(lambda: gateway.read("incidents", state=state, kind=kind, limit=limit))

    @server.tool(annotations=read)
    async def get_incident(incident_id: Any = None) -> dict[str, Any]:
        """Read the current incident summary by its internal ID."""

        async def read_one():
            return await gateway.read("incidents", id=str(identifier(incident_id)), limit=1)

        return await guarded(read_one)

    @server.tool(annotations=read)
    async def list_makers(limit: Any = 50) -> dict[str, Any]:
        """Read bounded maker summaries with masked destinations."""
        return await guarded(lambda: gateway.read("makers", limit=limit))

    @server.tool(annotations=read)
    async def get_cash() -> dict[str, Any]:
        """Read observed available, unreserved, reserved cash and freshness flags."""
        return await guarded(lambda: gateway.read("cash", limit=1))

    @server.tool(annotations=read)
    async def get_proposal(proposal_id: Any = None) -> dict[str, Any]:
        """Read a proposal's status, verdict and evaluated shield rules."""

        async def read_one():
            return await gateway.read("proposals", id=str(identifier(proposal_id)), limit=1)

        return await guarded(read_one)

    @server.tool(annotations=read)
    async def list_actions() -> dict[str, Any]:
        """Read CounterSign action names, parameter shapes and plain-English meanings."""
        return action_listing()

    @server.tool(annotations=proposal)
    async def propose(
        ctx: Context,
        incident_id: Any = None,
        action: Any = None,
        params: Any = None,
        rationale: Any = None,
        evidence_refs: Any = None,
    ) -> dict[str, Any]:
        """Preview or record an untrusted proposal for shield judgement on an existing incident."""

        async def submit():
            if incident_id is None:
                raise GatewayError("INVALID_REQUEST")
            return await gateway.propose(
                incident_id, action, params, rationale, evidence_refs, client=caller(ctx)
            )

        return await guarded(submit)

    @server.tool(annotations=proposal)
    async def propose_refund(
        ctx: Context, paypal_dispute_id: Any = None, rationale: Any = None, refund_to: Any = None
    ) -> dict[str, Any]:
        """Propose ACCEPT_CLAIM; optional destination requests are judged as untrusted policy input."""

        async def submit():
            params = {"paypal_dispute_id": paypal_dispute_id}
            if refund_to is not None:
                params["refund_to"] = refund_to
            return await gateway.propose(
                None, "ACCEPT_CLAIM", params, rationale, [], client=caller(ctx)
            )

        return await guarded(submit)

    @server.tool(annotations=proposal)
    async def propose_payout_run(
        ctx: Context, week_start: Any = None, rationale: Any = None
    ) -> dict[str, Any]:
        """Propose the exact existing planned run for an ISO week-start date; owner countersign required."""

        async def submit():
            params = await gateway.planned_run(week_start)
            return await gateway.propose(
                None, "SCHEDULE_PAYOUT_RUN", params, rationale, [], client=caller(ctx)
            )

        return await guarded(submit)

    @server.tool(annotations=proposal)
    async def propose_cancel_unclaimed(
        ctx: Context, payout_item_id: Any = None, rationale: Any = None
    ) -> dict[str, Any]:
        """Propose cancellation of an unclaimed payout item for shield judgement."""
        return await guarded(
            lambda: gateway.propose(
                None,
                "CANCEL_UNCLAIMED_PAYOUT",
                {"payout_item_id": payout_item_id},
                rationale,
                [],
                client=caller(ctx),
            )
        )

    @server.tool(annotations=proposal)
    async def propose_evidence(
        ctx: Context,
        paypal_dispute_id: Any = None,
        rationale: Any = None,
        evidence_refs: Any = None,
        evidences: Any = None,
    ) -> dict[str, Any]:
        """Propose stored, matching shipment evidence for shield judgement; tracking references required."""

        async def submit():
            params = {"paypal_dispute_id": paypal_dispute_id}
            if evidences is not None:
                params["evidences"] = evidences
            return await gateway.propose(
                None, "PROVIDE_EVIDENCE", params, rationale, evidence_refs, client=caller(ctx)
            )

        return await guarded(submit)

    # Derive accepted keyword sets from the registered functions without accepting
    # SDK-ignored extra arguments or returning its input-validation diagnostics.
    import inspect

    for function in (
        list_incidents,
        get_incident,
        list_makers,
        get_cash,
        get_proposal,
        list_actions,
        propose,
        propose_refund,
        propose_payout_run,
        propose_cancel_unclaimed,
        propose_evidence,
    ):
        allowed_arguments[function.__name__] = set(inspect.signature(function).parameters) - {"ctx"}
    return server


# Avoid recursive shadowing by the MCP tool with the same public name.
action_listing = list_actions


async def check_tools(server):
    names = [tool.name for tool in await server.list_tools()]
    if len(names) != len(ALLOWED_TOOLS) or set(names) != ALLOWED_TOOLS:
        raise GatewayError("GATEWAY_TOOL_SET_MISMATCH")


def http_app(server):
    # Only trusted deployment settings extend the SDK's DNS-rebinding allowlist.
    hosts = ["127.0.0.1:*", "localhost:*"]
    origins = ["http://127.0.0.1:*", "http://localhost:*"]
    origin = os.environ.get("COUNTERSIGN_GATEWAY_PUBLIC_ORIGIN", "")
    render_host = os.environ.get("RENDER_EXTERNAL_HOSTNAME", "")
    if not origin and render_host:
        origin = "https://" + render_host
    if origin:
        parsed = urlsplit(origin)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise GatewayError("GATEWAY_PUBLIC_ORIGIN_INVALID")
        hosts.append(parsed.netloc)
        origins.append("https://" + parsed.netloc)
    return server.streamable_http_app(
        streamable_http_path="/",
        json_response=True,
        stateless_http=True,
        max_request_body_size=131072,
        transport_security=TransportSecuritySettings(allowed_hosts=hosts, allowed_origins=origins),
    )


def mount_http(app, child):
    async def slash(request):
        # An exact route precedes the static catch-all, which otherwise answers
        # POST /mcp with 405 before Starlette gets to its trailing-slash redirect.
        return RedirectResponse("/mcp/", status_code=307)

    app.add_route("/mcp", slash, methods=["GET", "POST", "DELETE"])
    app.mount("/mcp", child, name="mcp-gateway")
