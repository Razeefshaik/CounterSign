"""Explicit async pool lifecycle and transaction ownership."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from countersign.config import Settings

Connection = AsyncConnection[dict[str, Any]]


class UnsupportedDatabase(ValueError):
    """This build supports PostgreSQL 16 only."""


async def _configure(conn: Connection) -> None:
    if conn.info.server_version // 10000 != 16:
        raise UnsupportedDatabase("CounterSign requires PostgreSQL 16")


class Database:
    """Open with `async with Database(settings) as db`, then use `async with db.tx()`."""

    def __init__(
        self,
        settings: Settings | str,
        *,
        min_size: int = 1,
        max_size: int = 10,
        timeout: float = 30.0,
    ):
        conninfo = settings.database_url if isinstance(settings, Settings) else settings
        self.pool = AsyncConnectionPool(
            conninfo,
            min_size=min_size,
            max_size=max_size,
            timeout=timeout,
            open=False,
            kwargs={"autocommit": True, "row_factory": dict_row},
            configure=_configure,
        )

    def __repr__(self):
        return "Database(conninfo='[REDACTED]')"

    async def open(self) -> None:
        try:
            await self.pool.open(wait=True)
        except BaseException:
            await self.pool.close()
            raise

    async def close(self) -> None:
        await self.pool.close()

    async def __aenter__(self):
        await self.open()
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        await self.close()

    def tx(self):
        return tx(self.pool)


@asynccontextmanager
async def tx(pool: AsyncConnectionPool) -> AsyncIterator[Connection]:
    """Borrow a connection; commit on success and roll back on error, then return it."""
    async with pool.connection() as conn, conn.transaction():
        yield conn
