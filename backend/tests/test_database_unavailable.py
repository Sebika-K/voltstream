"""A busy or unreachable database must produce a controlled 503, not a 500
(Contract section 51; found by the Roadmap 5.3 failure test)."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.errors import DATABASE_UNAVAILABLE_ERRORS
from app.db.session import engine

BATCH = {
    "events": [
        {
            "event_id": str(uuid.uuid4()),
            "battery_id": "BAT-000001",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "state_of_charge": 55.0,
            "voltage": 49.5,
            "current": 12.3,
            "power_kw": 1.5,
            "temperature_c": 26.0,
            "health_percent": 99.0,
            "status": "CHARGING",
        }
    ]
}

ERRORS = [
    PoolTimeoutError("QueuePool limit of size 5 overflow 10 reached"),
    OperationalError("SELECT 1", {}, Exception("connection lost")),
    InterfaceError("SELECT 1", {}, Exception("connection closed")),
]


@pytest.mark.parametrize("error", ERRORS, ids=lambda e: type(e).__name__)
async def test_a_database_outage_returns_a_controlled_503(client, monkeypatch, caplog, error):
    caplog.set_level(logging.INFO)

    async def _boom(session, batch):
        raise error

    monkeypatch.setattr("app.api.telemetry.ingest_telemetry_batch", _boom)

    response = await client.post("/api/v1/telemetry/batch", json=BATCH)

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "DATABASE_UNAVAILABLE",
            "message": "The database is temporarily unavailable. Please try again shortly.",
        }
    }
    assert response.headers["x-request-id"]  # still traceable
    (record,) = [r for r in caplog.records if r.getMessage() == "database_unavailable_for_request"]
    assert record.levelno == logging.ERROR
    assert record.error_code == "DATABASE_UNAVAILABLE"


async def test_a_real_bug_is_still_a_500_not_a_503(client, monkeypatch):
    async def _bug(session, batch):
        raise ValueError("a genuine programming error")

    monkeypatch.setattr("app.api.telemetry.ingest_telemetry_batch", _bug)

    # The test client re-raises unhandled server errors by default; a real server
    # turns them into a 500, which is what we are checking is still the case.
    with pytest.raises(ValueError):
        await client.post("/api/v1/telemetry/batch", json=BATCH)


async def test_real_pool_exhaustion_raises_an_error_we_handle():
    """Not a mock: really exhaust a one-connection pool and check that the error
    SQLAlchemy raises is one of the errors registered for the 503 handler."""
    tiny = create_async_engine(
        engine.url, pool_size=1, max_overflow=0, pool_timeout=0.2
    )
    try:
        async with tiny.connect():  # holds the only connection
            with pytest.raises(Exception) as caught:
                async with tiny.connect():
                    pass
        assert isinstance(caught.value, DATABASE_UNAVAILABLE_ERRORS)
    finally:
        await tiny.dispose()
