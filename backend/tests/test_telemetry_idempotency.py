"""Integration tests for telemetry idempotency (Roadmap 1.8, Contract section 17).

These prove the specific gap 1.8 closes: sending the exact same telemetry
event twice must succeed both times and must never produce two rows or let
the second delivery overwrite the first. That's what "retry-safe" means --
a simulator (or a flaky network) resending an event it already sent should
never be something the backend treats as an error.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest_asyncio
from sqlalchemy import delete, func, select

from app.db.session import async_session_maker
from app.models.battery import Battery
from app.models.telemetry import Telemetry

TEST_BATTERY_ID = "BAT-900003"


def _valid_event_payload(**overrides) -> dict:
    payload = {
        "event_id": str(uuid.uuid4()),
        "battery_id": TEST_BATTERY_ID,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "state_of_charge": 60.0,
        "voltage": 49.0,
        "current": 8.0,
        "power_kw": 1.0,
        "temperature_c": 25.0,
        "health_percent": 100.0,
        "status": "CHARGING",
    }
    payload.update(overrides)
    return payload


async def _cleanup() -> None:
    async with async_session_maker() as session:
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
    await _cleanup()
    await _register_test_battery()
    yield
    await _cleanup()


async def test_sending_the_same_event_twice_succeeds_both_times(client):
    payload = _valid_event_payload()

    first = await client.post("/api/v1/telemetry", json=payload)
    second = await client.post("/api/v1/telemetry", json=payload)

    assert first.status_code == 201  # newly created
    assert second.status_code == 200  # already existed -- a safe no-op, not an error


async def test_duplicate_delivery_does_not_create_a_second_row(client):
    payload = _valid_event_payload()

    await client.post("/api/v1/telemetry", json=payload)
    await client.post("/api/v1/telemetry", json=payload)
    await client.post("/api/v1/telemetry", json=payload)  # a third delivery, for good measure

    async with async_session_maker() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(Telemetry)
            .where(Telemetry.event_id == uuid.UUID(payload["event_id"]))
        )
    assert count == 1


async def test_duplicate_delivery_does_not_overwrite_the_original_reading(client):
    original = _valid_event_payload(state_of_charge=60.0, power_kw=1.0, status="CHARGING")
    # Same event_id, but different readings -- as if a retried request somehow
    # carried different numbers. ON CONFLICT DO NOTHING means the ORIGINAL
    # values win; the retry's (different) values are simply discarded.
    retried_with_different_values = {
        **original,
        "state_of_charge": 12.0,
        "power_kw": -3.0,
        "status": "DISCHARGING",
    }

    await client.post("/api/v1/telemetry", json=original)
    response = await client.post("/api/v1/telemetry", json=retried_with_different_values)
    assert response.status_code == 200

    async with async_session_maker() as session:
        stored = await session.get(Telemetry, uuid.UUID(original["event_id"]))

    assert stored.state_of_charge == 60.0
    assert stored.power_kw == 1.0
    assert stored.status == "CHARGING"


async def test_duplicate_event_id_with_a_different_battery_id_keeps_the_original_battery(client):
    # event_id is globally unique (Contract section 4.2) -- even a resend that
    # claims a different (but real, registered) battery_id must not reassign
    # an already-stored event to that other battery. Both battery_ids need to
    # actually be registered here, because the Contract's own processing
    # order (section 15) checks "battery exists" before it ever gets to the
    # duplicate-event_id logic -- an unregistered battery_id would be
    # rejected with 404 regardless of whether event_id was a duplicate.
    other_battery_id = "BAT-900004"
    async with async_session_maker() as session:
        session.add(
            Battery(
                battery_id=other_battery_id,
                capacity_kwh=10.0,
                max_power_kw=5.0,
                nominal_voltage=48.0,
                profile_type="RESIDENTIAL",
                created_at=datetime.now(timezone.utc),
            )
        )
        await session.commit()

    try:
        original = _valid_event_payload(battery_id=TEST_BATTERY_ID)
        await client.post("/api/v1/telemetry", json=original)

        conflicting = {**original, "battery_id": other_battery_id}
        response = await client.post("/api/v1/telemetry", json=conflicting)
        assert response.status_code == 200

        async with async_session_maker() as session:
            stored = await session.get(Telemetry, uuid.UUID(original["event_id"]))
        assert stored.battery_id == TEST_BATTERY_ID
    finally:
        async with async_session_maker() as session:
            await session.execute(delete(Battery).where(Battery.battery_id == other_battery_id))
            await session.commit()
