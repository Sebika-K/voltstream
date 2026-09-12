"""Integration tests for POST /api/v1/batteries (Roadmap 1.6).

These hit a real, running PostgreSQL database through the actual FastAPI app
-- no mocking, no SQLite -- per the TDD's testing strategy (section 29):
"Use PostgreSQL for database integration tests rather than relying
exclusively on SQLite." They need `DATABASE_URL` to point at a real, migrated
database (the one `alembic upgrade head` was already run against).

The `client` fixture used here comes from `conftest.py`.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import func, select

from app.db.session import async_session_maker
from app.models.battery import Battery

VALID_PAYLOAD = {
    "battery_id": "BAT-900001",
    "capacity_kwh": 10.0,
    "max_power_kw": 5.0,
    "nominal_voltage": 48.0,
    "latitude": 37.7749,
    "longitude": -122.4194,
    "installation_date": "2026-01-15",
    "profile_type": "RESIDENTIAL",
}


async def _delete_test_battery() -> None:
    async with async_session_maker() as session:
        existing = await session.get(Battery, VALID_PAYLOAD["battery_id"])
        if existing is not None:
            await session.delete(existing)
            await session.commit()


@pytest_asyncio.fixture(autouse=True)
async def _clean_slate():
    # Runs before AND after every test in this file. Before, in case a
    # previous failed run left the row behind (which would make "registering
    # a new battery" tests fail for the wrong reason); after, so this file
    # never leaves data behind for other tests or a real run of the app.
    await _delete_test_battery()
    yield
    await _delete_test_battery()


async def test_registering_a_new_battery_returns_201_with_its_data(client):
    response = await client.post("/api/v1/batteries", json=VALID_PAYLOAD)
    assert response.status_code == 201
    body = response.json()
    assert body["battery_id"] == VALID_PAYLOAD["battery_id"]
    assert body["capacity_kwh"] == VALID_PAYLOAD["capacity_kwh"]
    assert body["profile_type"] == "RESIDENTIAL"
    assert "created_at" in body


async def test_registered_battery_is_actually_persisted_in_postgres(client):
    await client.post("/api/v1/batteries", json=VALID_PAYLOAD)

    async with async_session_maker() as session:
        stored = await session.get(Battery, VALID_PAYLOAD["battery_id"])

    assert stored is not None
    assert stored.max_power_kw == VALID_PAYLOAD["max_power_kw"]
    assert stored.profile_type == "RESIDENTIAL"


async def test_reregistering_with_identical_metadata_is_idempotent(client):
    first = await client.post("/api/v1/batteries", json=VALID_PAYLOAD)
    second = await client.post("/api/v1/batteries", json=VALID_PAYLOAD)

    assert first.status_code == 201
    assert second.status_code == 200  # not another 201 -- nothing new was created
    assert second.json()["battery_id"] == VALID_PAYLOAD["battery_id"]


async def test_idempotent_reregistration_does_not_create_a_duplicate_row(client):
    await client.post("/api/v1/batteries", json=VALID_PAYLOAD)
    await client.post("/api/v1/batteries", json=VALID_PAYLOAD)

    async with async_session_maker() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(Battery)
            .where(Battery.battery_id == VALID_PAYLOAD["battery_id"])
        )
    assert count == 1


async def test_reregistering_with_conflicting_metadata_returns_409(client):
    await client.post("/api/v1/batteries", json=VALID_PAYLOAD)

    conflicting = {**VALID_PAYLOAD, "capacity_kwh": 999.0}
    response = await client.post("/api/v1/batteries", json=conflicting)

    assert response.status_code == 409
    body = response.json()
    assert body["error"]["code"] == "BATTERY_CONFIG_CONFLICT"
    assert VALID_PAYLOAD["battery_id"] in body["error"]["message"]


async def test_conflicting_registration_does_not_modify_the_stored_battery(client):
    await client.post("/api/v1/batteries", json=VALID_PAYLOAD)
    await client.post("/api/v1/batteries", json={**VALID_PAYLOAD, "capacity_kwh": 999.0})

    async with async_session_maker() as session:
        stored = await session.get(Battery, VALID_PAYLOAD["battery_id"])
    # The Contract: registration MUST NOT silently modify immutable config.
    assert stored.capacity_kwh == VALID_PAYLOAD["capacity_kwh"]


@pytest.mark.parametrize("bad_battery_id", ["not-a-battery-id", "BAT-1", "BAT-1234567", ""])
async def test_invalid_battery_id_format_is_rejected(client, bad_battery_id):
    response = await client.post(
        "/api/v1/batteries", json={**VALID_PAYLOAD, "battery_id": bad_battery_id}
    )
    assert response.status_code == 422


async def test_invalid_profile_type_is_rejected(client):
    response = await client.post(
        "/api/v1/batteries", json={**VALID_PAYLOAD, "profile_type": "NUCLEAR"}
    )
    assert response.status_code == 422


@pytest.mark.parametrize("field", ["capacity_kwh", "max_power_kw", "nominal_voltage"])
async def test_non_positive_ratings_are_rejected(client, field):
    response = await client.post("/api/v1/batteries", json={**VALID_PAYLOAD, field: 0})
    assert response.status_code == 422


async def test_missing_required_field_is_rejected(client):
    payload = {k: v for k, v in VALID_PAYLOAD.items() if k != "capacity_kwh"}
    response = await client.post("/api/v1/batteries", json=payload)
    assert response.status_code == 422


async def test_optional_location_fields_may_be_omitted(client):
    payload = {k: v for k, v in VALID_PAYLOAD.items() if k not in ("latitude", "longitude")}
    response = await client.post("/api/v1/batteries", json=payload)
    assert response.status_code == 201
    body = response.json()
    assert body["latitude"] is None
    assert body["longitude"] is None
