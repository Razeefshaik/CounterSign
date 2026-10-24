"""Exact, immutable read-tool registry. Tool names never grant write authority."""

import inspect
import json
from collections.abc import Awaitable, Callable, Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from types import MappingProxyType

from countersign.agent.safety import sanitize

TOOLKIT_TOOLS = frozenset(
    {"list_disputes", "get_dispute", "get_shipment_tracking", "get_order_details", "list_transactions"}
)
CUSTOM_TOOLS = frozenset({"get_payout_item", "query_money_graph", "simulate"})
ALLOWED_TOOLS = TOOLKIT_TOOLS | CUSTOM_TOOLS
_ERROR_CODES = frozenset(
    {"TOOLKIT_SET_MISMATCH", "TOOL_SET_MISMATCH", "TOOL_NOT_ALLOWED", "INVALID_TOOL_ARGUMENTS",
     "TOOL_CALL_FAILED", "TOOLKIT_CONFIG_INVALID", "TOOLKIT_CLOSED", "TOOLKIT_NOT_READY",
     "TOOLKIT_START_FAILED", "TOOLKIT_CALL_FAILED", "TOOLKIT_MESSAGE_INVALID",
     "TOOLKIT_DEPENDENCIES_MISSING", "TOOLKIT_VERSION_UNSUPPORTED", "TOOLKIT_REQUEST_BLOCKED"}
)


class ToolError(ValueError):
    """Fixed error codes: never echo a tool argument or dependency exception."""

    def __init__(self, code="TOOL_CALL_FAILED"):
        super().__init__(code if isinstance(code, str) and code in _ERROR_CODES else "TOOL_CALL_FAILED")


def check_toolkit_names(names: Iterable[str]) -> None:
    listed = list(names)
    if (
        any(type(name) is not str for name in listed)
        or len(listed) != len(TOOLKIT_TOOLS)
        or set(listed) != TOOLKIT_TOOLS
    ):
        raise ToolError("TOOLKIT_SET_MISMATCH")


@dataclass(frozen=True, repr=False)
class ReadTool:
    name: str
    description: str
    schema: Mapping
    handler: Callable[[dict], Awaitable[object]] = field(repr=False)

    def __repr__(self):
        return "ReadTool(values=<REDACTED>)"


class ToolRegistry:
    """Exactly the five D13 toolkit reads and three application-owned reads.

    The definitions given to the model are copies. Mutating them cannot replace
    a registered handler, change its name or add another callable capability.
    """

    def __init__(self, tools: Iterable[ReadTool]):
        try:
            listed = tuple(tools)
            valid = (
                len(listed) == len(ALLOWED_TOOLS)
                and all(isinstance(tool, ReadTool) for tool in listed)
                and {tool.name for tool in listed} == ALLOWED_TOOLS
                and all(
                    type(tool.name) is str
                    and isinstance(tool.description, str)
                    and isinstance(tool.schema, Mapping)
                    and tool.schema.get("type") == "object"
                    and callable(tool.handler)
                    for tool in listed
                )
            )
            if not valid:
                raise ValueError
            self._tools = MappingProxyType(
                {
                    tool.name: ReadTool(
                        tool.name, tool.description, deepcopy(dict(tool.schema)), tool.handler
                    )
                    for tool in listed
                }
            )
            # Definitions must be bounded JSON too; arbitrary dependency objects
            # never reach the chat adapter through their representations.
            json.dumps(self.schemas, allow_nan=False)
        except Exception:  # noqa: BLE001 - reject arbitrary dependency objects without their reprs.
            raise ToolError("TOOL_SET_MISMATCH") from None

    def __repr__(self):
        return "ToolRegistry(values=<REDACTED>)"

    @property
    def names(self) -> frozenset[str]:
        return frozenset(self._tools)

    @property
    def schemas(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": tool.description,
                    "parameters": deepcopy(dict(tool.schema)),
                },
            }
            for name, tool in sorted(self._tools.items())
        ]

    async def invoke(self, name: str, arguments: dict) -> dict:
        if type(name) is not str or name not in self._tools:
            raise ToolError("TOOL_NOT_ALLOWED")
        if type(arguments) is not dict:
            raise ToolError("INVALID_TOOL_ARGUMENTS")
        tool = self._tools[name]
        try:
            # Bound and validate JSON without changing identifiers or silently
            # replacing private values in a query's input.
            encoded = json.dumps(arguments, allow_nan=False)
            if len(encoded.encode("utf-8")) > 65536:
                raise ValueError
            copied = json.loads(encoded)
            properties = tool.schema.get("properties", {})
            required = tool.schema.get("required", ())
            if (
                not isinstance(properties, Mapping)
                or not set(required) <= copied.keys()
                or (
                    tool.schema.get("additionalProperties") is False
                    and not copied.keys() <= properties.keys()
                )
            ):
                raise ValueError
        except Exception:  # noqa: BLE001 - malformed mappings never expose their private values.
            raise ToolError("INVALID_TOOL_ARGUMENTS") from None
        try:
            result = tool.handler(copied)
            if not inspect.isawaitable(result):
                raise ValueError
            output = sanitize(await result)
            return output if isinstance(output, dict) else {"result": output}
        except ToolError:
            raise
        except Exception:  # noqa: BLE001 - a tool exception may contain credentials or buyer text.
            raise ToolError("TOOL_CALL_FAILED") from None
