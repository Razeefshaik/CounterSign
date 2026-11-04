"""C65: guarded read proxy fails closed and never forwards private fields."""

from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from countersign.api.paypal_calls import build_router
from mock_paypal.call_log import append, document
from mock_paypal.store import MockStore


@pytest.mark.asyncio
@pytest.mark.parametrize(("mode", "mock", "database", "expected"), [
    ("live",False,"countersign",404), ("demo",True,"production",404),
    ("demo",False,"countersign_demo",404), ("demo",True,"countersign_demo",200),
])
async def test_route_only_exists_for_allowed_disposable_demo(monkeypatch, mode, mock, database, expected):
    store = MockStore()
    append(store, method="GET", route="/v1/reporting/balances", status=200)
    value = document(store)
    original = httpx.AsyncClient
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(200, json=value)
    monkeypatch.setattr("countersign.api.paypal_calls.httpx.AsyncClient", lambda **kw:original(transport=httpx.MockTransport(respond), **kw))
    settings = SimpleNamespace(mode=mode, database_url="postgresql://fake:fake@localhost/"+database,
                               paypal=SimpleNamespace(is_mock=mock,base_url="http://127.0.0.1:8099"))
    app = FastAPI()
    app.include_router(build_router(settings), prefix="/api/v1")
    async with original(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/demo/paypal-calls")
        assert response.status_code == expected
        if expected == 200:
            assert response.json() == value
            assert str(requests[0].url) == "http://127.0.0.1:8099/_mock/paypal-calls"
            value["rows"][0]["Authorization"] = "private-token"
            response = await client.get("/api/v1/demo/paypal-calls")
            assert response.status_code == 503 and "private-token" not in response.text
            assert (await client.post("/api/v1/demo/paypal-calls")).status_code == 405
            assert (await client.get("/api/v1/demo/paypal-calls?url=other")).status_code == 422
        else:
            assert not requests
