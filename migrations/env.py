"""Run raw SQL migrations with a supplied connection or runtime DATABASE_URL."""

import os

from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.pool import NullPool

config = context.config


def database_url():
    value = config.attributes.get("database_url") or os.environ.get("DATABASE_URL")
    if not value:
        raise RuntimeError("DATABASE_URL is required for migrations")
    try:
        url = make_url(value)
    except ArgumentError:
        raise RuntimeError("DATABASE_URL must be a PostgreSQL connection URL") from None
    if url.get_backend_name() != "postgresql":
        raise RuntimeError("Migrations require PostgreSQL 16")
    return url.set(drivername="postgresql+psycopg")


def migrate(connection: Connection) -> None:
    version = connection.dialect.server_version_info
    if not version or version[0] != 16:
        raise RuntimeError("Migrations require PostgreSQL 16")
    context.configure(
        connection=connection,
        target_metadata=None,
        # Independent integration-test schemas keep their own revision table.
        version_table_schema=config.attributes.get("version_table_schema"),
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    context.configure(
        url=database_url(),
        target_metadata=None,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    supplied_connection = config.attributes.get("connection")
    if supplied_connection is not None:
        migrate(supplied_connection)
    else:
        engine = create_engine(database_url(), poolclass=NullPool, echo=False)
        try:
            with engine.connect() as connection:
                migrate(connection)
        finally:
            engine.dispose()
