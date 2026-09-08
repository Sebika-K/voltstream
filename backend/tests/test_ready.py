"""Tests for GET /ready (readiness) and the underlying database connectivity check."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import create_async_engine

from app.db.session import ping_engine


async def test_ready_returns_200_when_database_available(client):
    response = await client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "database": "connected"}


async def test_ready_returns_503_when_database_reports_unavailable(client, monkeypatch):
    async def _unavailable() -> bool:
        return False

    # Patch the name as imported into the endpoint module, matching how it's called.
    monkeypatch.setattr("app.api.system.check_database_connection", _unavailable)

    response = await client.get("/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "not_ready", "database": "unavailable"}


async def test_ping_engine_succeeds_against_real_database():
    from app.db.session import engine

    assert await ping_engine(engine, timeout=2.0) is True


async def test_ping_engine_fails_gracefully_against_unreachable_database():
    """Exercises real socket-level failure handling, not a mocked stand-in.

    Port 1 is a reserved/unroutable port that nothing should ever be listening on,
    so this reliably reproduces a real "database unavailable" condition end to end.
    """
    unreachable_engine = create_async_engine(
        "postgresql+asyncpg://voltstream:voltstream@127.0.0.1:1/voltstream",
    )
    try:
        assert await ping_engine(unreachable_engine, timeout=1.0) is False
    finally:
        await unreachable_engine.dispose()
