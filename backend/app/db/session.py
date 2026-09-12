"""Async SQLAlchemy engine/session management.

Uses SQLAlchemy 2.x's async API with the ``asyncpg`` driver, per the TDD. A single
module-level engine is created from application settings and reused for the lifetime of
the process; sessions are created per unit of work via ``get_db_session``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings

logger = logging.getLogger(__name__)

settings = get_settings()

# `pool_pre_ping` guards against stale connections (e.g. after a Postgres restart) being
# handed out to callers. Pool sizing is configurable rather than fixed, per the TDD.
engine: AsyncEngine = create_async_engine(
    settings.DATABASE_URL,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_timeout=settings.DB_POOL_TIMEOUT_SECONDS,
    pool_pre_ping=True,
    future=True,
)

async_session_maker = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding a request-scoped ``AsyncSession``.

    First used by the battery registration endpoint (Roadmap 1.6); every
    later database-backed endpoint depends on this same function rather than
    constructing its own session.
    """
    async with async_session_maker() as session:
        yield session


async def ping_engine(target_engine: AsyncEngine, timeout: float) -> bool:
    """Run ``SELECT 1`` against ``target_engine``, bounded by ``timeout`` seconds.

    Returns ``True`` on success and ``False`` on *any* failure (connection refused,
    authentication failure, timeout, etc.) -- this function never raises, which is what
    lets readiness checks report a clean 503 instead of a 500.
    """
    try:

        async def _probe() -> None:
            async with target_engine.connect() as connection:
                await connection.execute(text("SELECT 1"))

        await asyncio.wait_for(_probe(), timeout=timeout)
        return True
    except Exception:  # noqa: BLE001 - a readiness probe must never raise
        logger.warning("Database connectivity check failed", exc_info=True)
        return False


async def check_database_connection() -> bool:
    """Check connectivity for the application's configured database engine.

    This is what backs ``GET /ready``. It intentionally does not affect ``GET /health``,
    which reports liveness independently of PostgreSQL availability.
    """
    return await ping_engine(engine, settings.DB_READY_TIMEOUT_SECONDS)
