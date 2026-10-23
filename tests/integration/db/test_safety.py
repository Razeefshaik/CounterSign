"""Persistence safety checks which intentionally need neither PostgreSQL nor Docker."""

from concurrent.futures import ThreadPoolExecutor
from enum import StrEnum
from uuid import UUID

import pytest

from countersign.config import Settings, SettingsError, load_settings
from countersign.db import Database, Repositories, Repository, StaleRow, cas_transition, ids, uuid7
from countersign.fsm import IllegalTransition, Machine


class State(StrEnum):
    OPEN = "OPEN"
    RESERVED = "RESERVED"


class Event(StrEnum):
    DISPUTE_OPENED = "DISPUTE_OPENED"


MACHINE = Machine("test", State, Event, {(State.OPEN, Event.DISPUTE_OPENED): State.RESERVED})


class NoSql:
    async def execute(self, *args):
        raise AssertionError("No SQL should be issued")


def test_settings_are_runtime_only_and_masked(monkeypatch):
    fake = "postgresql://fake-user:fake-password@localhost/fake_test"
    monkeypatch.setenv("DATABASE_URL", fake)
    settings = load_settings()
    assert settings.database_url == fake
    assert "fake" not in repr(settings)
    assert "REDACTED" in repr(settings)
    assert "fake" not in repr(Database(settings))
    monkeypatch.delenv("DATABASE_URL")
    with pytest.raises(SettingsError, match="DATABASE_URL is required"):
        load_settings()


@pytest.mark.parametrize("value", ["", "sqlite:///private", "private-password", "https://host/db"])
def test_config_errors_do_not_echo_values(value):
    with pytest.raises(SettingsError) as error:
        Settings(value)
    assert "private" not in str(error.value)


def test_uuid7_layout_and_monotonicity_under_clock_regression(monkeypatch):
    timestamp_ms = 1_800_000_000_000
    monkeypatch.setattr(ids, "_last_ms", -1)
    monkeypatch.setattr(ids, "_random", 0)
    monkeypatch.setattr(ids.time, "time_ns", lambda: timestamp_ms * 1_000_000)
    first = uuid7()
    timestamp_ms -= 1000
    second = uuid7()
    assert isinstance(first, UUID)
    assert first.version == second.version == 7
    assert first.variant == "specified in RFC 4122"
    assert first.int < second.int
    assert first.int >> 80 == 1_800_000_000_000


def test_uuid7_is_unique_across_threads():
    with ThreadPoolExecutor(max_workers=8) as executor:
        values = list(executor.map(lambda _: uuid7(), range(2000)))
    assert len(set(values)) == 2000
    assert all(value.version == 7 for value in values)


@pytest.mark.asyncio
async def test_illegal_transition_happens_before_any_sql_or_table_validation():
    with pytest.raises(IllegalTransition):
        await cas_transition(
            NoSql(), MACHINE, "unsafe; DROP TABLE makers", uuid7(), "RESERVED", 1, "DISPUTE_OPENED"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "table", ["operations", "proposals", "payout_attempts", "makers; SELECT 1"]
)
async def test_cas_rejects_tables_without_state_and_version(table):
    with pytest.raises(ValueError, match="approved table"):
        await cas_transition(NoSql(), MACHINE, table, uuid7(), "OPEN", 1, "DISPUTE_OPENED")


@pytest.mark.asyncio
async def test_cas_uses_parameters_and_reports_zero_rows():
    class Cursor:
        async def fetchone(self):
            return None

    class Connection:
        async def execute(self, query, params):
            self.query, self.params = query.as_string(), params
            return Cursor()

    conn = Connection()
    row_id = uuid7()
    with pytest.raises(StaleRow):
        await cas_transition(conn, MACHINE, "obligations", row_id, "OPEN", 3, "DISPUTE_OPENED")
    assert 'UPDATE "obligations"' in conn.query
    assert "version = version + 1" in conn.query
    assert conn.params == ("RESERVED", row_id, "OPEN", 3)


@pytest.mark.asyncio
async def test_repository_rejects_identifiers_generated_columns_and_float_money():
    with pytest.raises(ValueError, match="Unknown repository table"):
        Repository(NoSql(), "makers; DROP TABLE makers")
    repo = Repository(NoSql(), "reservations")
    with pytest.raises(ValueError, match="Unknown repository column"):
        await repo.find({"state OR true": "ACTIVE"})
    with pytest.raises(ValueError, match="Generated"):
        await repo.insert(affects_cash=True)
    for value in (1.5, True, "10"):
        with pytest.raises(TypeError, match="integer minor"):
            await repo.insert(amount_minor=value)
    with pytest.raises(ValueError, match="keys or version"):
        await Repository(NoSql(), "makers").update(uuid7(), version=100)
    with pytest.raises(ValueError, match="limit"):
        await repo.find(limit=0)


@pytest.mark.asyncio
async def test_append_only_repositories_and_key_shapes():
    repos = Repositories(NoSql())
    with pytest.raises(ValueError, match="append-only"):
        await repos.audit_log.delete(1)
    with pytest.raises(ValueError, match="append-only"):
        await repos.ledger_postings.update(uuid7(), amount_minor=1)
    with pytest.raises(ValueError, match="no row key"):
        await repos.ledger_postings.get(uuid7())
    with pytest.raises(ValueError, match="key shape"):
        await repos.payout_line_obligations.get(uuid7())
    with pytest.raises(AttributeError):
        _ = repos.unknown
