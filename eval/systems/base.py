"""CONTRACTS section 6.2 system boundary; credentials here are fake mock values only."""

from dataclasses import dataclass
from typing import Literal, Protocol
from urllib.parse import unquote, urlsplit

import httpx


def require_local(url: str) -> None:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Evaluation requires a local HTTP mock")
    _ = parsed.port


class MockControlClient:
    """Typed control calls; the client may use an in-process ASGI transport."""

    def __init__(self, client: httpx.AsyncClient):
        require_local(str(client.base_url))
        self.client = client
        self.now = ""

    async def request(self, method: str, path: str, body: dict | None = None) -> dict:
        clean = unquote(path)
        if (
            method not in {"GET", "POST", "DELETE"}
            or not clean.startswith("/_mock/")
            or ".." in clean
            or "?" in clean
            or "#" in clean
            or "\\" in clean
        ):
            raise ValueError("Invalid mock control request")
        response = await self.client.request(method, path, json=body)
        if response.is_error:
            raise RuntimeError("Mock control request failed")
        return response.json()

    async def reset(self, seed: int) -> dict:
        return await self.request("POST", "/_mock/reset", {"seed": seed})

    async def seed(self, state: dict) -> dict:
        return await self.request("POST", "/_mock/seed", state)

    async def clock(self) -> dict:
        result = await self.request("GET", "/_mock/clock")
        self.now = result["now"]
        return result

    async def advance(self, seconds: int) -> dict:
        result = await self.request("POST", "/_mock/clock/advance", {"seconds": seconds})
        self.now = result["now"]
        return result

    async def ground_truth(self) -> dict:
        return await self.request("GET", "/_mock/ground-truth")

    async def fault(self, body: dict) -> dict:
        return await self.request("POST", "/_mock/faults", body)

    async def webhook_fault(self, body: dict) -> dict:
        return await self.request("POST", "/_mock/webhook-faults", body)


@dataclass(repr=False)
class EvalEnv:
    scenario: dict
    mock_base_url: str
    mock: MockControlClient
    paypal_client_id: str
    paypal_client_secret: str
    webhook_url: str | None
    llm: object | None
    run_id: str
    k_index: int


@dataclass
class OwnerRequest:
    id: str
    action: str
    params: dict
    total: dict | None
    rationale: str


@dataclass
class ActionRecord:
    at: str
    action: str
    params: dict
    outcome: Literal["ok", "blocked", "failed", "unknown"]
    approved_by_owner: bool


@dataclass
class SystemTrace:
    actions: list[ActionRecord]
    owner_requests: int
    errors: list[str]
    llm_calls: int
    tokens_in: int
    tokens_out: int


class System(Protocol):
    name: Literal["baseline_llm", "rules_only", "countersign"]

    async def setup(self, env: EvalEnv) -> None: ...
    async def on_step(self, step: dict, env: EvalEnv) -> None: ...
    async def on_webhook(self, headers: dict[str, str], raw_body: bytes) -> None: ...
    async def pending_owner_requests(self) -> list[OwnerRequest]: ...
    async def owner_decide(self, request_id: str, approve: bool) -> None: ...
    async def teardown(self) -> SystemTrace: ...
