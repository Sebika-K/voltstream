"""Integration tests for POST /api/v1/telemetry (Roadmap 1.7).

Like the battery registration tests, these hit a real, running PostgreSQL
database through the actual FastAPI app. This is the Roadmap's "first major
project checkpoint" -- the test that matters most here is simply: does an
event generated the way the simulator would generate one actually end up as
a row in the `telemetry` table.

Deliberately NOT tested here: sending the same event twice. Roadmap 1.7
doesn't include idempotent duplicate handling (that's 1.8) -- today, a
duplicate `event_id` raises a database integrity error rather than failing
gracefully. That gap is real and is closed in the next step, not hidden by
avoiding the test; there's just nothing meaningful to assert about it yet
beyond "this isn't handled," which the next step's tests will cover properly.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import delete

from app.db.session import async_session_maker
from app.models.battery import Battery
from app.models.telemetry import Telemetry

TEST_BATTERY_ID = "BAT-900002"


def _valid_event_payload(**overrides) -> dict:
    payload = {
        "event_id": str(uuid.uuid4()),
        "battery_id": TEST_BATTERY_ID,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "state_of_charge": 55.0,
        "voltage": 49.5,
        "current": 12.3,
        "power_kw": 1.5,
        "temperature_c": 26.0,
        "health_percent": 99.0,
        "status": "CHARGING",
    }
    payload.update(overrides)
    return payload


async def _cleanup() -> None:
    async with async_session_maker() as session:
        # Telemetry rows first -- the foreign key to `batteries` would block
        # deleting the battery otherwise.
        await session.execute(delete(Telemetry).where(Telemetry.battery_id == TEST_BATTERY_ID))
        await session.execute(delete(Battery).where(Battery.battery_id == TEST_BATTERY_ID))
        await session.commit()


async def _register_test_battery() -> None:
    async with async_session_maker() as session:
        session.add(
            Battery(
                battery_id=TEST_BATTERY_ID,
                capacity_kwh=10.0,
                max_power_kw=5.0,
                nominal_voltage=48.0,
                profile_type="RESIDENTIAL",
                created_at=datetime.now(timezone.utc),
            )
        )
        await session.commit()


@pytest_asyncio.fixture(autouse=True)
async def _clean_slate():
    # Every test in this file starts with exactly one registered battery
    # (TEST_BATTERY_ID) and zero telemetry rows, and leaves the same behind
    # for the next file -- regardless of whether the test passed or failed.
    await _cleanup()
    await _register_test_battery()
    yield
    await _cleanup()


async def test_ingesting_a_valid_event_returns_201_and_an_ack(client):
    payload = _valid_event_payload()
    response = await client.post("/api/v1/telemetry", json=payload)

    assert response.status_code == 201
    body = response.json()
    assert body["event_id"] == payload["event_id"]
    assert body["battery_id"] == TEST_BATTERY_ID
    assert body["status"] == "accepted"


async def test_ingested_event_is_actually_persisted_with_correct_values(client):
    payload = _valid_event_payload(state_of_charge=42.5, power_kw=-2.0, status="DISCHARGING")
    await client.post("/api/v1/telemetry", json=payload)

    async with async_session_maker() as session:
        stored = await session.get(Telemetry, uuid.UUID(payload["event_id"]))

    assert stored is not None
    assert stored.battery_id == TEST_BATTERY_ID
    assert stored.state_of_charge == 42.5
    assert stored.power_kw == -2.0
    assert stored.status == "DISCHARGING"


async def test_ingesting_for_an_unregistered_battery_returns_404_with_error_envelope(client):
    payload = _valid_event_payload(battery_id="BAT-999999")
    response = await client.post("/api/v1/telemetry", json=payload)

    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "BATTERY_NOT_FOUND"
    assert "BAT-999999" in body["error"]["message"]


async def test_event_for_unregistered_battery_is_not_stored(client):
    payload = _valid_event_payload(battery_id="BAT-999999")
    await client.post("/api/v1/telemetry", json=payload)

    async with async_session_maker() as session:
        stored = await session.get(Telemetry, uuid.UUID(payload["event_id"]))
    assert stored is None


async def test_multiple_events_for_the_same_battery_are_all_stored(client):
    first = _valid_event_payload(state_of_charge=50.0)
    second = _valid_event_payload(state_of_charge=51.0)

    await client.post("/api/v1/telemetry", json=first)
    await client.post("/api/v1/telemetry", json=second)

    async with async_session_maker() as session:
        first_row = await session.get(Telemetry, uuid.UUID(first["event_id"]))
        second_row = await session.get(Telemetry, uuid.UUID(second["event_id"]))

    # Telemetry is append-only history -- two distinct readings for the same
    # battery are two rows, not one row being overwritten.
    assert first_row is not None
    assert second_row is not None
    assert first_row.event_id != second_row.event_id


async def test_out_of_order_timestamps_are_both_accepted(client):
    # Contract section 18: historical telemetry accepts late-arriving events.
    # This endpoint doesn't maintain any "current state" yet (that's Roadmap
    # 2.1), so there's no ordering logic to trip over -- both simply get
    # stored, which is exactly what should happen either way.
    now = datetime.now(timezone.utc)
    earlier = now.replace(microsecond=0)
    later = now

    later_event = _valid_event_payload(timestamp=later.isoformat())
    earlier_event = _valid_event_payload(timestamp=earlier.isoformat())

    later_response = await client.post("/api/v1/telemetry", json=later_event)
    earlier_response = await client.post("/api/v1/telemetry", json=earlier_event)

    assert later_response.status_code == 201
    assert earlier_response.status_code == 201


async def test_malformed_event_is_rejected_with_422_before_touching_the_database(client):
    payload = _valid_event_payload(state_of_charge=150.0)  # out of the 0-100 bound
    response = await client.post("/api/v1/telemetry", json=payload)

    assert response.status_code == 422
    async with async_session_maker() as session:
        stored = await session.get(Telemetry, uuid.UUID(payload["event_id"]))
    assert stored is None


@pytest.mark.parametrize("missing_field", ["event_id", "battery_id", "status"])
async def test_missing_required_field_is_rejected(client, missing_field):
    payload = _valid_event_payload()
    del payload[missing_field]
    response = await client.post("/api/v1/telemetry", json=payload)
    assert response.status_code == 422
