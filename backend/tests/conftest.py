"""Shared pytest fixtures for the backend test suite.

Tests talk to the ASGI app in-process via httpx (no real network socket), and assume a
real, reachable PostgreSQL instance is configured through ``DATABASE_URL`` -- per the
testing contract, integration-style checks use actual PostgreSQL rather than SQLite or a
purely mocked database.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


@pytest.fixture(autouse=True)
def _isolate_settings_cache():
    """Ensure the cached Settings singleton is fresh for each test module.

    Prevents test-order-dependent surprises if a future test needs to patch
    environment variables before constructing Settings.
    """
    from app.core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
