"""Tests for GET /health (liveness)."""

from __future__ import annotations


async def test_health_returns_200_and_ok_status(client):
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_health_route_has_no_database_dependency(client):
    """/health must not depend on app.db.session.check_database_connection at all.

    We assert this structurally (the endpoint module never imports the DB check),
    which is a stronger guarantee than mocking the DB call and hoping it isn't used.
    """
    import inspect

    from app.api import system

    source = inspect.getsource(system.health)
    assert "check_database_connection" not in source
