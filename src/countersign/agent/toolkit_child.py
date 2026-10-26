"""Standalone isolated child for the pinned toolkit's genuine LangChain tools.

Run by toolkit.py with python -I. This module deliberately imports no application
packages; importing it for a test does not construct a toolkit or open a socket.
"""

import base64
import contextlib
import importlib.metadata
import json
import logging
import math
import os
import re
import sys
from urllib.parse import urlencode, urlsplit

VERSION = "1.11.0"
SANDBOX = "https://api-m.sandbox.paypal.com"
MAX_MESSAGE_BYTES = 262144
TOOL_NAMES = frozenset(
    {
        "list_disputes",
        "get_dispute",
        "get_shipment_tracking",
        "get_order_details",
        "list_transactions",
    }
)
ACTIONS = {
    "disputes": {"list": True, "get": True},
    "shipment": {"get": True},
    "orders": {"get": True},
    "transactions": {"list": True},
}
_READ_PATHS = (
    re.compile(r"/v1/customer/disputes(?:/[A-Za-z0-9_-]+)?"),
    re.compile(r"/v1/shipping/trackers"),
    re.compile(r"/v2/checkout/orders/[A-Za-z0-9_-]+"),
    re.compile(r"/v1/reporting/transactions"),
)
_EMAIL = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PRIVATE_FIELDS = frozenset(
    {
        "authorization",
        "proxyauthorization",
        "accesstoken",
        "refreshtoken",
        "clientid",
        "clientsecret",
        "secret",
        "password",
        "apikey",
        "token",
        "paypalauthassertion",
    }
)


class ChildError(ValueError):
    """Contains a fixed protocol code only."""


def validated_origin(value):
    try:
        parsed = urlsplit(value)
        port = parsed.port
        valid = (
            type(value) is str
            and parsed.scheme in {"http", "https"}
            and parsed.hostname in {"127.0.0.1", "localhost", "api-m.sandbox.paypal.com"}
            and parsed.username is None
            and parsed.password is None
            and not parsed.query
            and not parsed.fragment
            and parsed.path in {"", "/"}
            and (port is None or 1 <= port <= 65535)
            and (
                parsed.hostname != "api-m.sandbox.paypal.com"
                or (parsed.scheme == "https" and port in {None, 443})
            )
        )
        if valid:
            suffix = f":{port}" if port is not None else ""
            return f"{parsed.scheme}://{parsed.hostname}{suffix}"
    except (TypeError, ValueError, AttributeError):
        raise ChildError("TOOLKIT_CONFIG_INVALID") from None
    raise ChildError("TOOLKIT_CONFIG_INVALID")


def check_names(names):
    names = list(names)
    if (
        len(names) != len(TOOL_NAMES)
        or any(type(name) is not str for name in names)
        or set(names) != TOOL_NAMES
    ):
        raise ChildError("TOOLKIT_SET_MISMATCH")


def clean(value, secrets=(), *, depth=0):
    """Bounded JSON with no credentials, full emails or arbitrary object reprs."""
    if depth > 16:
        raise ChildError("TOOLKIT_MESSAGE_INVALID")
    if value is None or type(value) in (bool, int):
        return value
    if type(value) is float:
        # PayPal money uses decimal strings; JSON numbers elsewhere stay finite.
        if not math.isfinite(value):
            raise ChildError("TOOLKIT_MESSAGE_INVALID")
        return value
    if type(value) is str:
        text = value
        for secret in sorted((s for s in secrets if s), key=len, reverse=True):
            text = text.replace(secret, "<REDACTED>")
        text = _EMAIL.sub("m***@example.com", text)
        if len(text) > 32768:
            raise ChildError("TOOLKIT_MESSAGE_INVALID")
        return text
    if type(value) is list:
        if len(value) > 1000:
            raise ChildError("TOOLKIT_MESSAGE_INVALID")
        return [clean(item, secrets, depth=depth + 1) for item in value]
    if type(value) is dict:
        if len(value) > 1000 or any(type(key) is not str for key in value):
            raise ChildError("TOOLKIT_MESSAGE_INVALID")
        return {
            clean(key, secrets, depth=depth + 1): (
                "<REDACTED>"
                if re.sub(r"[^a-z]", "", key.lower()) in _PRIVATE_FIELDS
                else clean(item, secrets, depth=depth + 1)
            )
            for key, item in value.items()
        }
    raise ChildError("TOOLKIT_MESSAGE_INVALID")


class RequestGuard:
    """A second boundary: OAuth plus fixed GET paths at one validated origin."""

    def __init__(self, base_url, secrets):
        self.base_url = validated_origin(base_url)
        self.secrets = secrets
        self.ready = False
        self.blocked = False

    def check(self, method, url):
        try:
            parsed = urlsplit(url)
            origin = validated_origin(f"{parsed.scheme}://{parsed.netloc}")
            if (
                not self.ready
                or origin != self.base_url
                or parsed.username is not None
                or parsed.password is not None
                or parsed.fragment
                or not (
                    (method.upper() == "POST" and parsed.path == "/v1/oauth2/token")
                    or (
                        method.upper() == "GET"
                        and any(pattern.fullmatch(parsed.path) for pattern in _READ_PATHS)
                    )
                )
            ):
                raise ValueError
        except (ValueError, TypeError, AttributeError):
            self.blocked = True
            raise ChildError("TOOLKIT_REQUEST_BLOCKED") from None

    def wrap(self, original):
        def request(session, method, url, *args, **kwargs):
            self.check(method, url)
            # The toolkit uses requests.get/post. Disable ambient proxies/netrc,
            # redirects and unbounded reads, including for OAuth credentials.
            session.trust_env = False
            kwargs.update(timeout=(3, 8), allow_redirects=False, verify=True, stream=True)
            response = original(session, method, url, *args, **kwargs)
            try:
                content = bytearray()
                for chunk in response.iter_content(chunk_size=65536):
                    content.extend(chunk)
                    if len(content) > MAX_MESSAGE_BYTES // 2:
                        raise ChildError("TOOLKIT_MESSAGE_INVALID")
                response._content = bytes(content)
                response._content_consumed = True
                if urlsplit(url).path == "/v1/oauth2/token":
                    try:
                        token = response.json().get("access_token")
                    except (ValueError, TypeError, AttributeError):
                        token = None
                    if type(token) is str and token:
                        self.secrets.add(token)
            finally:
                response.close()
            return response

        return request


class Runtime:
    """Adapter instance stays inside its non-tracing process."""

    def __init__(self, settings):
        if type(settings) is not dict or set(settings) != {
            "base_url",
            "client_id",
            "client_secret",
        }:
            raise ChildError("TOOLKIT_CONFIG_INVALID")
        self.base_url = validated_origin(settings["base_url"])
        client_id, secret = settings["client_id"], settings["client_secret"]
        if any(
            type(value) is not str or not value or len(value) > 4096
            for value in (client_id, secret)
        ):
            raise ChildError("TOOLKIT_CONFIG_INVALID")
        basic = base64.b64encode(f"{client_id}:{secret}".encode()).decode()
        self.secrets = {client_id, secret, basic}
        try:
            installed = importlib.metadata.version("paypal-agent-toolkit")
        except importlib.metadata.PackageNotFoundError:
            raise ChildError("TOOLKIT_DEPENDENCIES_MISSING") from None
        if installed != VERSION:
            raise ChildError("TOOLKIT_VERSION_UNSUPPORTED")
        try:
            import requests
            from langsmith import tracing_context
            from paypal_agent_toolkit.langchain.toolkit import PayPalToolkit
            from paypal_agent_toolkit.shared.configuration import Configuration, Context
        except ImportError:
            raise ChildError("TOOLKIT_DEPENDENCIES_MISSING") from None
        self._tracing = tracing_context
        self._guard = RequestGuard(self.base_url, self.secrets)
        requests.sessions.Session.request = self._guard.wrap(requests.sessions.Session.request)
        with self._tracing(enabled=False):
            toolkit = PayPalToolkit(
                client_id=client_id,
                secret=secret,
                configuration=Configuration(
                    actions={product: dict(verbs) for product, verbs in ACTIONS.items()},
                    context=Context(sandbox=True),
                ),
            )
            client = toolkit.get_paypal_api()._paypal_client
            if client.base_url != SANDBOX:
                raise ChildError("TOOLKIT_CONFIG_INVALID")
            tools = toolkit.get_tools()
        check_names(tool.name for tool in tools)
        self._tools = {tool.name: tool for tool in tools}
        client.base_url = self.base_url
        self._client = client
        # SDK 1.11.0's read handlers expect two helpers its HTTP client lacks:
        # tracking by order calls get_order_details(), and transaction-ID search
        # calls get(payload=...) then decodes a JSON string. Keep the genuine
        # handlers and normalize only those fixed read requests here.
        original_get = client.get

        def get(uri, payload=None):
            if payload is None:
                return original_get(uri)
            if (
                uri != "/v1/reporting/transactions"
                or type(payload) is not dict
                or not payload.keys()
                <= {
                    "transaction_id",
                    "transaction_status",
                    "start_date",
                    "end_date",
                    "page_size",
                    "page",
                }
            ):
                self._guard.blocked = True
                raise ChildError("TOOLKIT_REQUEST_BLOCKED")
            query = {key: value for key, value in payload.items() if value is not None}
            return json.dumps(original_get(uri + "?" + urlencode(query)))

        client.get = get
        client.get_order_details = lambda order_id: original_get("/v2/checkout/orders/" + order_id)
        self._definitions = []
        for name, tool in sorted(self._tools.items()):
            schema = tool.args_schema.model_json_schema()
            if type(schema) is not dict or schema.get("type") != "object":
                raise ChildError("TOOLKIT_SET_MISMATCH")
            schema["additionalProperties"] = False
            self._definitions.append(
                {"name": name, "description": tool.description, "schema": schema}
            )
        self._guard.ready = True

    def listing(self):
        return clean(self._definitions, self.secrets)

    def invoke(self, name, arguments):
        check_names(self._tools)
        if self._guard.blocked:
            raise ChildError("TOOLKIT_REQUEST_BLOCKED")
        # Context(sandbox=True) is required to disable live APIs, but the
        # selected HTTP origin is independent. Refuse SDK/config drift before
        # an OAuth request, rather than falling back to a sandbox default.
        if self._client.base_url != self.base_url:
            raise ChildError("TOOLKIT_CONFIG_INVALID")
        if type(name) is not str or name not in self._tools or type(arguments) is not dict:
            raise ChildError("INVALID_TOOL_ARGUMENTS")
        schema = next(item["schema"] for item in self._definitions if item["name"] == name)
        if (
            not arguments.keys() <= schema.get("properties", {}).keys()
            or not set(schema.get("required", ())) <= arguments.keys()
        ):
            raise ChildError("INVALID_TOOL_ARGUMENTS")
        try:
            with self._tracing(enabled=False):
                output = self._tools[name].invoke(
                    arguments, config={"callbacks": [], "tags": [], "metadata": {}}
                )
            # Some SDK handlers catch transport exceptions and return text.
            # A swallowed guard refusal must never become a recoverable read.
            if self._guard.blocked:
                raise ChildError("TOOLKIT_REQUEST_BLOCKED")
            if isinstance(output, str):
                if output.startswith("Error executing PayPalTool"):
                    raise ChildError("TOOLKIT_CALL_FAILED")
                output = json.loads(output)
            result = clean(output, self.secrets)
            encoded = json.dumps(result, allow_nan=False).encode("utf-8")
            if len(encoded) > MAX_MESSAGE_BYTES - 1024:
                raise ChildError("TOOLKIT_MESSAGE_INVALID")
            return result
        except ChildError:
            raise
        except Exception:  # noqa: BLE001 - adapter failures must not carry private request values.
            if self._guard.blocked:
                raise ChildError("TOOLKIT_REQUEST_BLOCKED") from None
            raise ChildError("TOOLKIT_CALL_FAILED") from None


def main():
    # Do this before importing the SDK, even if someone launches this file
    # directly rather than using the parent gateway's minimal environment.
    for key in list(os.environ):
        if key.upper().startswith(("PAYPAL_", "LANGCHAIN_", "LANGSMITH_", "SBX_")):
            del os.environ[key]
    logging.disable(logging.CRITICAL)
    source, destination = sys.stdin.buffer, sys.stdout.buffer
    runtime = None
    with open(os.devnull, "w", encoding="utf-8") as sink:
        while True:
            raw = source.readline(MAX_MESSAGE_BYTES + 1)
            if not raw:
                return 0
            recoverable = False
            try:
                if len(raw) > MAX_MESSAGE_BYTES:
                    raise ChildError("TOOLKIT_MESSAGE_INVALID")
                message = json.loads(raw)
                if type(message) is not dict:
                    raise ChildError("TOOLKIT_MESSAGE_INVALID")
                with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                    if message.get("op") == "open" and runtime is None:
                        runtime = Runtime(message.get("settings"))
                        response = {"ok": True, "tools": runtime.listing()}
                    elif message.get("op") == "invoke" and runtime is not None:
                        response = {
                            "ok": True,
                            "output": runtime.invoke(message.get("tool"), message.get("arguments")),
                        }
                    else:
                        raise ChildError("TOOLKIT_MESSAGE_INVALID")
            except ChildError as error:
                response = {"ok": False, "error": error.args[0]}
                recoverable = (
                    runtime is not None and type(message) is dict and message.get("op") == "invoke"
                    and error.args[0] in {"TOOLKIT_CALL_FAILED", "INVALID_TOOL_ARGUMENTS"}
                )
            except Exception:  # noqa: BLE001 - never emit dependency exception text on the wire.
                response = {"ok": False, "error": "TOOLKIT_CALL_FAILED"}
            encoded = json.dumps(response, allow_nan=False).encode("utf-8") + b"\n"
            if len(encoded) > MAX_MESSAGE_BYTES:
                encoded = b'{"ok":false,"error":"TOOLKIT_MESSAGE_INVALID"}\n'
                recoverable = False
            destination.write(encoded)
            destination.flush()
            if response.get("ok") is not True and not recoverable:
                return 2


if __name__ == "__main__":
    sys.exit(main())
