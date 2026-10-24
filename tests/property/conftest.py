"""Reuse the existing disposable-schema and harness fixtures without editing C21 files."""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "integration"


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


fixtures = load("_c22_postgres_fixtures", "conftest.py")
schema_factory = fixtures.schema_factory
db_schema = fixtures.db_schema
test_db_url = fixtures.test_db_url
pytest_asyncio_loop_factories = fixtures.pytest_asyncio_loop_factories
world = load("_c22_harness_fixtures", "test_harness.py").world
