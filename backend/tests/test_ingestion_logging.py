"""Tests that important ingestion / failure behavior is traceable from logs
(Roadmap 5.2: "Important ingestion and failure behavior can be traced from logs").

Same approach as the other integration tests: real requests through the real app
against real PostgreSQL. The difference is what gets asserted -- not the response,
but the structured log records the request leaves behind.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings
from app.db.session import async_session_maker, ping_engine
from app.models.battery import Battery
from app.models.battery_current_state import BatteryCurrentState
from app.models.telemetry import Telemetry

LOGGED_BATTERY_ID = "BAT-900301"
UNKNOWN_BATTERY_ID = "BAT-900399"
REGISTERED_VIA_API_ID = "BAT-900302"
ALL_IDS = [LOGGED_BATTERY_ID, UNKNOWN_BATTERY_ID, REGISTERED_VIA_API_ID]


def _event(battery_id: str = LOGGED_BATTERY_ID, event_id: str | None = None) -> dict:
    return {
        "event_id": event_id or str(uuid.uuid4()),
        "battery_id": battery_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "state_of_charge": 55.0,
        "voltage": 49.5,
        "current": 12.3,
        "power_kw": 1.5,
        "temperature_c": 26.0,
        "health_percent": 99.0,
        "status": "CHARGING",
    }


def _records(caplog, message: str) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.getMessage() == message]


async def _cleanup() -> None:
    async with async_session_maker() as session:
        await session.execute(delete(Telemetry).where(Telemetry.battery_id.in_(ALL_IDS)))
        await session.execute(
            delete(BatteryCurrentState).where(BatteryCurrentState.battery_id.in_(ALL_IDS))
        )
        await session.execute(delete(Battery).where(Battery.battery_id.in_(ALL_IDS)))
        await session.commit()


@pytest_asyncio.fixture
async def registered_battery():
    await _cleanup()
    async with async_session_maker() as session:
        session.add(
            Battery(
                battery_id=LOGGED_BATTERY_ID,
                capacity_kwh=10.0,
                max_power_kw=5.0,
                nominal_voltage=48.0,
                profile_type="RESIDENTIAL",
                created_at=datetime.now(timezone.utc),
            )
        )
        await session.commit()
    yield
    await _cleanup()


@pytest_asyncio.fixture
async def clean_slate():
    await _cleanup()
    yield
    await _cleanup()


# --- ingestion ----------------------------------------------------------------


async def test_batch_logs_counts_and_duration(client, registered_battery, caplog):
    caplog.set_level(logging.INFO)
    repeated_id = str(uuid.uuid4())
    events = [_event(event_id=repeated_id), _event(event_id=repeated_id), _event()]

    response = await client.post("/api/v1/telemetry/batch", json={"events": events})

    assert response.status_code == 201
    (record,) = _records(caplog, "telemetry_batch_processed")
    assert record.received == 3
    assert record.inserted == 2
    assert record.duplicates == 1
    assert record.battery_count == 1
    assert record.duration_ms >= 0


async def test_single_event_logs_battery_and_event_ids(client, registered_battery, caplog):
    caplog.set_level(logging.INFO)
    event = _event()

    await client.post("/api/v1/telemetry", json=event)

    (record,) = _records(caplog, "telemetry_ingested")
    assert record.battery_id == LOGGED_BATTERY_ID
    assert record.event_id == event["event_id"]


async def test_retry_of_the_same_event_is_logged_as_a_duplicate(
    client, registered_battery, caplog
):
    caplog.set_level(logging.INFO)
    event = _event()

    await client.post("/api/v1/telemetry", json=event)
    await client.post("/api/v1/telemetry", json=event)

    assert len(_records(caplog, "telemetry_ingested")) == 1
    (duplicate,) = _records(caplog, "telemetry_duplicate_ignored")
    assert duplicate.event_id == event["event_id"]


# --- rejections ---------------------------------------------------------------


async def test_unknown_battery_is_logged_as_a_rejection_naming_it(client, clean_slate, caplog):
    caplog.set_level(logging.INFO)

    response = await client.post("/api/v1/telemetry", json=_event(UNKNOWN_BATTERY_ID))

    assert response.status_code == 404
    (record,) = _records(caplog, "request_rejected")
    assert record.levelno == logging.WARNING
    assert record.status_code == 404
    assert record.error_code == "BATTERY_NOT_FOUND"
    assert UNKNOWN_BATTERY_ID in record.error
    assert record.method == "POST"
    assert record.path == "/api/v1/telemetry"


async def test_oversized_batch_is_logged_as_a_rejection(client, clean_slate, caplog):
    caplog.set_level(logging.INFO)
    too_many = get_settings().MAX_BATCH_SIZE + 1

    response = await client.post(
        "/api/v1/telemetry/batch", json={"events": [_event() for _ in range(too_many)]}
    )

    assert response.status_code == 413
    (record,) = _records(caplog, "request_rejected")
    assert record.error_code == "BATCH_TOO_LARGE"
    assert record.status_code == 413


# --- registration -------------------------------------------------------------


def _registration_payload() -> dict:
    return {
        "battery_id": REGISTERED_VIA_API_ID,
        "capacity_kwh": 10.0,
        "max_power_kw": 5.0,
        "nominal_voltage": 48.0,
        "profile_type": "RESIDENTIAL",
    }


async def test_new_registration_is_logged_but_a_repeat_is_not_at_info(client, clean_slate, caplog):
    caplog.set_level(logging.INFO)

    first = await client.post("/api/v1/batteries", json=_registration_payload())
    second = await client.post("/api/v1/batteries", json=_registration_payload())

    assert first.status_code == 201
    assert second.status_code == 200
    (record,) = _records(caplog, "battery_registered")
    assert record.battery_id == REGISTERED_VIA_API_ID
    assert record.levelno == logging.INFO


async def test_conflicting_registration_is_logged_as_a_rejection(client, clean_slate, caplog):
    await client.post("/api/v1/batteries", json=_registration_payload())
    caplog.clear()
    caplog.set_level(logging.INFO)

    response = await client.post(
        "/api/v1/batteries", json={**_registration_payload(), "capacity_kwh": 99.0}
    )

    assert response.status_code == 409
    (record,) = _records(caplog, "request_rejected")
    assert record.error_code == "BATTERY_CONFIG_CONFLICT"
    assert REGISTERED_VIA_API_ID in record.error


# --- database failure ---------------------------------------------------------


async def test_unreachable_database_is_logged_as_an_error(caplog):
    caplog.set_level(logging.INFO)
    unreachable = create_async_engine("postgresql+asyncpg://voltstream:voltstream@127.0.0.1:1/x")
    try:
        assert await ping_engine(unreachable, timeout=1.0) is False
    finally:
        await unreachable.dispose()

    (record,) = _records(caplog, "database_unavailable")
    assert record.levelno == logging.ERROR
    assert record.exc_info is not None  # the underlying error is kept for diagnosis
