"""Integration tests for POST /api/v1/telemetry/batch (Roadmap 1.9, Contract section 16).

These hit a real, running PostgreSQL database through the actual FastAPI app, same
as the single-ingestion and idempotency tests. What matters here is specific to
batching: several events land in the database from one request, duplicates inside
the batch and duplicates against already-stored events are both counted rather than
rejected, an oversized batch is refused before touching the database, and a
structurally invalid event or an unregistered battery anywhere in the batch means
NOTHING in the batch gets persisted.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest_asyncio
from sqlalchemy import delete, func, select

from app.core.config import get_settings
from app.db.session import async_session_maker
from app.models.battery import Battery
from app.models.telemetry import Telemetry

TEST_BATTERY_ID = "BAT-900005"
OTHER_BATTERY_ID = "BAT-900006"


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


def _valid_batch(count: int, **event_overrides) -> dict:
    return {"events": [_valid_event_payload(**event_overrides) for _ in range(count)]}


async def _cleanup() -> None:
    async with async_session_maker() as session:
        # Telemetry rows first -- the foreign key to `batteries` would block
        # deleting the battery otherwise.
        await session.execute(
            delete(Telemetry).where(Telemetry.battery_id.in_([TEST_BATTERY_ID, OTHER_BATTERY_ID]))
        )
        await session.execute(
            delete(Battery).where(Battery.battery_id.in_([TEST_BATTERY_ID, OTHER_BATTERY_ID]))
        )
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


async def test_a_batch_of_new_events_is_fully_persisted_and_returns_201(client):
    batch = _valid_batch(5)
    response = await client.post("/api/v1/telemetry/batch", json=batch)

    assert response.status_code == 201
    body = response.json()
    assert body == {"received": 5, "inserted": 5, "duplicates": 0}

    async with async_session_maker() as session:
        count = await session.scalar(
            select(func.count()).select_from(Telemetry).where(Telemetry.battery_id == TEST_BATTERY_ID)
        )
    assert count == 5


async def test_duplicate_event_ids_within_the_same_batch_are_counted_not_rejected(client):
    repeated_id = str(uuid.uuid4())
    batch = {
        "events": [
            _valid_event_payload(event_id=repeated_id, state_of_charge=10.0),
            _valid_event_payload(event_id=repeated_id, state_of_charge=99.0),
            _valid_event_payload(),
        ]
    }

    response = await client.post("/api/v1/telemetry/batch", json=batch)

    assert response.status_code == 201
    assert response.json() == {"received": 3, "inserted": 2, "duplicates": 1}

    # DO NOTHING, not DO UPDATE -- the first occurrence of the repeated
    # event_id in the batch wins, the second is silently dropped.
    async with async_session_maker() as session:
        stored = await session.get(Telemetry, uuid.UUID(repeated_id))
    assert stored.state_of_charge == 10.0


async def test_events_already_persisted_from_an_earlier_request_count_as_duplicates(client):
    first_batch = _valid_batch(3)
    await client.post("/api/v1/telemetry/batch", json=first_batch)

    # Resend the same 3 events plus 2 brand-new ones.
    second_batch = {"events": first_batch["events"] + _valid_batch(2)["events"]}
    response = await client.post("/api/v1/telemetry/batch", json=second_batch)

    assert response.status_code == 201  # the 2 new ones still get created
    assert response.json() == {"received": 5, "inserted": 2, "duplicates": 3}


async def test_a_batch_that_is_entirely_duplicates_returns_200_not_201(client):
    batch = _valid_batch(3)
    await client.post("/api/v1/telemetry/batch", json=batch)

    response = await client.post("/api/v1/telemetry/batch", json=batch)

    assert response.status_code == 200
    assert response.json() == {"received": 3, "inserted": 0, "duplicates": 3}


async def test_a_batch_over_the_maximum_size_is_rejected_with_413_and_persists_nothing(client):
    max_size = get_settings().MAX_BATCH_SIZE
    batch = _valid_batch(max_size + 1)

    response = await client.post("/api/v1/telemetry/batch", json=batch)

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "BATCH_TOO_LARGE"

    async with async_session_maker() as session:
        count = await session.scalar(
            select(func.count()).select_from(Telemetry).where(Telemetry.battery_id == TEST_BATTERY_ID)
        )
    assert count == 0


async def test_a_batch_at_exactly_the_maximum_size_is_accepted(client):
    max_size = get_settings().MAX_BATCH_SIZE
    batch = _valid_batch(max_size)

    response = await client.post("/api/v1/telemetry/batch", json=batch)

    assert response.status_code == 201
    assert response.json()["received"] == max_size


async def test_an_empty_batch_is_rejected_with_422(client):
    response = await client.post("/api/v1/telemetry/batch", json={"events": []})
    assert response.status_code == 422


async def test_one_structurally_invalid_event_fails_the_whole_batch_with_422(client):
    batch = {
        "events": [
            _valid_event_payload(),
            _valid_event_payload(state_of_charge=150.0),  # out of the 0-100 bound
            _valid_event_payload(),
        ]
    }

    response = await client.post("/api/v1/telemetry/batch", json=batch)

    assert response.status_code == 422
    async with async_session_maker() as session:
        count = await session.scalar(
            select(func.count()).select_from(Telemetry).where(Telemetry.battery_id == TEST_BATTERY_ID)
        )
    assert count == 0


async def test_an_unregistered_battery_anywhere_in_the_batch_fails_it_entirely(client):
    batch = {
        "events": [
            _valid_event_payload(),
            _valid_event_payload(battery_id="BAT-999999"),
        ]
    }

    response = await client.post("/api/v1/telemetry/batch", json=batch)

    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "BATTERY_NOT_FOUND"
    assert "BAT-999999" in body["error"]["message"]

    # Nothing persisted -- not even the event for the battery that *is* registered.
    async with async_session_maker() as session:
        count = await session.scalar(
            select(func.count()).select_from(Telemetry).where(Telemetry.battery_id == TEST_BATTERY_ID)
        )
    assert count == 0


async def test_a_batch_spanning_multiple_registered_batteries_is_persisted_correctly(client):
    async with async_session_maker() as session:
        session.add(
            Battery(
                battery_id=OTHER_BATTERY_ID,
                capacity_kwh=10.0,
                max_power_kw=5.0,
                nominal_voltage=48.0,
                profile_type="COMMERCIAL",
                created_at=datetime.now(timezone.utc),
            )
        )
        await session.commit()

    batch = {
        "events": [
            _valid_event_payload(battery_id=TEST_BATTERY_ID),
            _valid_event_payload(battery_id=OTHER_BATTERY_ID),
        ]
    }
    response = await client.post("/api/v1/telemetry/batch", json=batch)

    assert response.status_code == 201
    assert response.json() == {"received": 2, "inserted": 2, "duplicates": 0}
