"""Isolated PostgreSQL 16 schemas; never inspect .env or migrate an existing schema."""

import asyncio
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from sqlalchemy import create_engine

ROOT = Path(__file__).resolve().parents[2]


@dataclass(repr=False)
class DatabaseSchema:
    url: str
    schema: str

    @property
    def conninfo(self):
        return make_conninfo(self.url, options=f"-csearch_path={self.schema},public")

    def connect(self, **kwargs):
        return psycopg.connect(self.conninfo, autocommit=True, row_factory=dict_row, **kwargs)

    def migrate(self, revision="head", *, downgrade=False):
        engine = create_engine(
            "postgresql+psycopg://",
            creator=lambda: psycopg.connect(self.conninfo),
            echo=False,
        )
        try:
            with engine.begin() as connection:
                config = Config(str(ROOT / "migrations/alembic.ini"))
                config.attributes["connection"] = connection
                config.attributes["version_table_schema"] = self.schema
                (command.downgrade if downgrade else command.upgrade)(config, revision)
        finally:
            engine.dispose()


@pytest.fixture
def schema_factory():
    url = os.environ.get("COUNTERSIGN_TEST_DATABASE_URL")
    if not url:
        pytest.skip("COUNTERSIGN_TEST_DATABASE_URL is unset")
    created = []

    def factory(revision="head"):
        schema = "cs_test_" + uuid4().hex
        with psycopg.connect(url, autocommit=True) as conn:
            if conn.info.server_version // 10000 != 16:
                pytest.fail("Integration tests require PostgreSQL 16")
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        database = DatabaseSchema(url, schema)
        created.append(database)
        database.migrate(revision)
        return database

    try:
        yield factory
    finally:
        for database in reversed(created):
            with psycopg.connect(url, autocommit=True) as conn:
                conn.execute(
                    sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(database.schema))
                )


@pytest.fixture
def base_schema(schema_factory):
    return schema_factory("0001")


@pytest.fixture
def db_schema(schema_factory):
    return schema_factory()


@pytest.fixture
def test_db_url(db_schema):
    return db_schema.conninfo


@pytest.hookimpl(optionalhook=True)
def pytest_asyncio_loop_factories():
    # psycopg async connections need selectors on Windows.
    if sys.platform == "win32":
        return {"selector": asyncio.SelectorEventLoop}
    return {"default": asyncio.new_event_loop}
