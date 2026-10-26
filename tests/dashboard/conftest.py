import asyncio
import importlib.util
import sys
from pathlib import Path

import httpx
import pytest
from db_support import database
from fastapi import FastAPI
from fastapi.testclient import TestClient

PATH = Path(__file__).resolve().parents[2] / "src/countersign/api/dashboard.py"
spec = importlib.util.spec_from_file_location("dashboard_under_test", PATH)
dashboard = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = dashboard
spec.loader.exec_module(dashboard)


@pytest.fixture
def source():
    return dashboard.FixtureSource()


@pytest.fixture
def client(source, request):
    if getattr(request, "param", "fixtures") == "database":
        yield request.getfixturevalue("database_client")
        return
    app = FastAPI()
    app.include_router(dashboard.build_router(source), prefix="/api/v1")
    with TestClient(app) as client:
        yield client


@pytest.fixture
def dashboard_database():
    with database() as db:
        yield db


@pytest.fixture
def database_client(dashboard_database):
    from types import SimpleNamespace

    runtime = SimpleNamespace(mode="demo", paypal=SimpleNamespace(is_mock=True))
    with asyncio.Runner(
        loop_factory=asyncio.SelectorEventLoop
        if sys.platform == "win32"
        else asyncio.new_event_loop
    ) as runner:
        source = dashboard.DbSource(dashboard_database.conninfo, runtime=runtime)
        runner.run(source.__aenter__())
        app = FastAPI()
        app.include_router(dashboard.build_router(source), prefix="/api/v1")
        http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )

        class Client:
            def get(self, *args, **kwargs):
                return runner.run(http.get(*args, **kwargs))

        try:
            yield Client()
        finally:
            runner.run(http.aclose())
            runner.run(source.__aexit__(None, None, None))
