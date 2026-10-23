import importlib.util
import sys
from pathlib import Path

import pytest
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
def client(source):
    app = FastAPI()
    app.include_router(dashboard.build_router(source), prefix="/api/v1")
    with TestClient(app) as client:
        yield client
