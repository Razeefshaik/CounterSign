"""Small independent checker for the JSON Schema keywords used by these fixtures.
No third-party schema dependency is required. Unsupported keywords fail closed.
"""

from datetime import datetime


def validate(value, schema):
    allowed = {
        "type",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "anyOf",
        "enum",
        "format",
        "minimum",
        "$schema",
        "title",
        "if",
        "then",
        "else",
        "not",
    }
    assert not schema.keys() - allowed, "Unsupported schema keyword"
    if "anyOf" in schema:
        for option in schema["anyOf"]:
            try:
                validate(value, option)
                break
            except AssertionError:
                pass
        else:
            raise AssertionError("No schema variant matches")
    if "not" in schema:
        try:
            validate(value, schema["not"])
        except AssertionError:
            pass
        else:
            raise AssertionError("Forbidden schema matches")
    if "if" in schema:
        try:
            validate(value, schema["if"])
        except AssertionError:
            branch = "else"
        else:
            branch = "then"
        if branch in schema:
            validate(value, schema[branch])
    types = schema.get("type")
    if types:
        types = [types] if isinstance(types, str) else types
        checks = {
            "object": isinstance(value, dict),
            "array": isinstance(value, list),
            "string": isinstance(value, str),
            "integer": type(value) is int,
            "number": type(value) in (int, float),
            "boolean": type(value) is bool,
            "null": value is None,
        }
        assert any(checks[type] for type in types), "Incorrect value type"
    if "enum" in schema:
        assert value in schema["enum"], "Invalid enum value"
    if "minimum" in schema:
        assert value >= schema["minimum"], "Below minimum"
    if isinstance(value, dict):
        assert set(schema.get("required", [])) <= value.keys(), "Missing required property"
        props = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            assert not value.keys() - props.keys(), "Unexpected property"
        for key, item in value.items():
            if key in props:
                validate(item, props[key])
            elif isinstance(schema.get("additionalProperties"), dict):
                validate(item, schema["additionalProperties"])
    if isinstance(value, list) and "items" in schema:
        for item in value:
            validate(item, schema["items"])
    if isinstance(value, str) and schema.get("format") == "date-time":
        assert value.endswith("Z"), "Time must use UTC Z suffix"
        assert datetime.fromisoformat(value).tzinfo is not None
