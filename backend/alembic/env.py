"""Alembic environment configuration.

Wired for SQLAlchemy 2.x async: the migration URL and target metadata come from the
application's own settings and declarative base (never duplicated/hard-coded here), so
Alembic and the running application always agree on where the database is and what the
schema should look like.

Alembic is the sole schema-migration mechanism for VoltStream -- the application does
not call ``Base.metadata.create_all()``.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.core.config import get_settings
from app.db.base import Base

# Importing the models package registers every ORM model's table onto
# Base.metadata -- without this import, target_metadata below would be
# empty and autogenerate would never see `batteries` or `telemetry`.
import app.models  # noqa: F401,E402

# Alembic Config object, providing access to values within alembic.ini.
config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Target metadata for 'autogenerate' support. Empty until the first ORM models are
# added in a later phase -- that's expected, not a bug.
target_metadata = Base.metadata


def get_url() -> str:
    """Resolve the database URL from application settings rather than alembic.ini."""
    return get_settings().DATABASE_URL


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emits SQL, no live DB connection)."""
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Run migrations in 'online' mode using an async engine/connection."""
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = get_url()

    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
