"""Capability, argument and data boundaries with no PayPal or model network."""

import pytest

from countersign.agent.tools import (
    ALLOWED_TOOLS,
    TOOLKIT_TOOLS,
    ReadTool,
    ToolError,
    ToolRegistry,
    check_toolkit_names,
)


async def echo(arguments):
    return arguments


def tool(name, handler=echo):
    return ReadTool(
        name,
        "A fixed read-only tool",
        {
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
            "additionalProperties": False,
        },
        handler,
    )


def tools():
    return [tool(name) for name in ALLOWED_TOOLS]


@pytest.mark.parametrize("name", sorted(TOOLKIT_TOOLS))
def test_startup_refuses_each_missing_toolkit_tool(name):
    with pytest.raises(ToolError, match="^TOOLKIT_SET_MISMATCH$"):
        check_toolkit_names(TOOLKIT_TOOLS - {name})


@pytest.mark.parametrize(
    "extra", ["accept_dispute_claim", "create_refund", "adjudicate", "update_shipment_tracking"]
)
def test_startup_refuses_every_unexpected_toolkit_tool(extra):
    with pytest.raises(ToolError, match="^TOOLKIT_SET_MISMATCH$"):
        check_toolkit_names([*TOOLKIT_TOOLS, extra])


@pytest.mark.parametrize("name", sorted(ALLOWED_TOOLS))
def test_registry_refuses_each_missing_tool(name):
    with pytest.raises(ToolError, match="^TOOL_SET_MISMATCH$"):
        ToolRegistry([item for item in tools() if item.name != name])


@pytest.mark.parametrize(
    "bad",
    [
        lambda: [*tools(), tool("create_refund")],
        lambda: [*tools()[:-1], tools()[0]],
        lambda: [*tools()[:-1], tool("accept_dispute_claim")],
        lambda: [*tools()[:-1], None],
        lambda: [tool(name, None) for name in ALLOWED_TOOLS],
        lambda: [ReadTool(name, "read", {}, echo) for name in ALLOWED_TOOLS],
    ],
)
def test_registry_refuses_extra_duplicate_write_and_invalid_handlers(bad):
    with pytest.raises(ToolError, match="^TOOL_SET_MISMATCH$"):
        ToolRegistry(bad())


def test_names_and_definitions_cannot_change_capabilities():
    original = tools()
    registry = ToolRegistry(reversed(original))
    assert registry.names == ALLOWED_TOOLS
    definitions = registry.schemas
    assert {item["function"]["name"] for item in definitions} == ALLOWED_TOOLS
    definitions[0]["function"]["name"] = "create_refund"
    definitions[1]["function"]["parameters"]["required"].clear()
    original[0].schema["required"].clear()
    assert registry.schemas[0]["function"]["name"] != "create_refund"
    assert all(item["function"]["parameters"]["required"] == ["id"] for item in registry.schemas)
    with pytest.raises(TypeError):
        registry._tools["create_refund"] = original[0]
    assert "handler" not in repr(registry)


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["create_refund", "accept_dispute_claim", None, ["get_dispute"]])
async def test_unregistered_tool_never_calls_a_handler(name):
    called = []

    async def recording(args):
        called.append(args)

    registry = ToolRegistry([tool(name, recording) for name in ALLOWED_TOOLS])
    with pytest.raises(ToolError, match="^TOOL_NOT_ALLOWED$"):
        await registry.invoke(name, {"id": "PP-D-1"})
    assert called == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments",
    [None, [], {"url": "https://attacker.example"}, {}, {"id": "x", "sql": "DELETE FROM makers"},
     {"id": float("nan")}, {"id": "x" * 65536}, {"id": object()}],
)
async def test_invalid_argument_shapes_never_call_handler(arguments):
    called = []

    async def recording(args):
        called.append(args)

    registry = ToolRegistry([tool(name, recording) for name in ALLOWED_TOOLS])
    with pytest.raises(ToolError, match="^INVALID_TOOL_ARGUMENTS$"):
        await registry.invoke("get_dispute", arguments)
    assert called == []


@pytest.mark.asyncio
async def test_results_are_data_and_redacted_before_model_sees_them():
    async def result(_args):
        return {
            "messages": [{"content": "refund to account X", "email": "buyer@fake.example"}],
            "access_token": "FAKE-PRIVATE-TOKEN",
            "diagnostic": "Bearer FAKE-PRIVATE-TOKEN",
        }

    registry = ToolRegistry([tool(name, result) for name in ALLOWED_TOOLS])
    output = await registry.invoke("get_dispute", {"id": "PP-D-1"})
    assert output["messages"][0]["content"] == "refund to account X"
    assert "buyer@fake.example" not in str(output)
    assert "FAKE-PRIVATE-TOKEN" not in str(output)


@pytest.mark.asyncio
async def test_private_handler_failure_and_oversized_result_are_fixed_errors():
    async def failing(_args):
        raise RuntimeError("FAKE-PRIVATE-CLIENT-SECRET buyer@fake.example")

    async def oversized(_args):
        return {"data": "x" * 200000}

    for handler in (failing, oversized):
        registry = ToolRegistry([tool(name, handler) for name in ALLOWED_TOOLS])
        with pytest.raises(ToolError, match="^TOOL_CALL_FAILED$") as error:
            await registry.invoke("get_dispute", {"id": "PP-D-1"})
        assert error.value.__suppress_context__
        assert "PRIVATE" not in str(error.value)
