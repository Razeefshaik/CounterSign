"""Strict output-only encoding; original application read schemas stay unchanged."""

import copy

from jsonschema import ValidationError, validate

from countersign.agent.models import ModelError


def strict_parameters(original):
    node = copy.deepcopy(original)
    node.pop("default", None)
    if node.get("type") == "object":
        # Arbitrary-key objects need a separate encoding; never silently narrow/widen them.
        if node.get("additionalProperties") is not False:
            raise ModelError("MODEL_TOOL_SCHEMA_UNSUPPORTED")
        required = node.get("required", [])
        properties = {}
        for name, child in node.get("properties", {}).items():
            encoded = strict_parameters(child)
            if name not in required:
                # A wrapper preserves explicit null separately from omission/defaults.
                encoded = {"anyOf": [
                    {"type": "object", "additionalProperties": False,
                     "properties": {"value": encoded}, "required": ["value"]},
                    {"type": "null"}],
                    "description": "Use null to omit this optional argument; otherwise use "
                                   "an object with value containing the argument, including explicit null."}
            properties[name] = encoded
        node["properties"] = properties
        node["required"] = list(properties)
    if "items" in node:
        node["items"] = strict_parameters(node["items"])
    if "anyOf" in node:
        node["anyOf"] = [strict_parameters(child) for child in node["anyOf"]]
    return node


def _decode(value, original):
    if original.get("type") == "object":
        result = {}
        for name, child in original.get("properties", {}).items():
            encoded = value[name]
            if name not in original.get("required", []):
                if encoded is None:
                    continue
                encoded = encoded["value"]
            result[name] = _decode(encoded, child)
        return result
    if "items" in original:
        return [_decode(item, original["items"]) for item in value]
    if "anyOf" in original:
        for child in original["anyOf"]:
            try:
                validate(value, strict_parameters(child))
            except ValidationError:
                continue
            return _decode(value, child)
        raise ModelError("INVALID_MODEL_RESPONSE")
    return value


def decode_calls(calls, tools):
    """Validate both wire arguments and original arguments before any tool executes."""
    definitions = {tool["function"]["name"]: tool["function"]["parameters"] for tool in tools}
    decoded = []
    try:
        for call in calls:
            original = definitions[call["name"]]
            validate(call["args"], strict_parameters(original))
            args = _decode(call["args"], original)
            validate(args, original)
            decoded.append({**call, "args": args})
    except (ValidationError, KeyError, TypeError):
        raise ModelError("INVALID_MODEL_RESPONSE") from None
    return decoded
