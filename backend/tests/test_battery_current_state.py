"""Integration tests for `battery_current_state` upkeep (Roadmap 2.1,
Contract sections 18, 19, 21).

These don't test a new endpoint -- 2.1 doesn't add one (that's 2.2's job).
They test the *side effect* every telemetry ingestion call now has: keeping
one up-to-date "latest state" row per battery, checked here by querying the
table directly, the same way a future `GET /api/v1/batteries/{id}` handler
eventually will.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest_asyncio
from sqlalchemy import delete

from app.db.session import async_session_maker
from app.models.battery import Battery
from app.models.battery_current_state import BatteryCurrentState
from app.models.telemetry import Telemetry

TEST_BATTERY_ID = "BAT-900007"
OTHER_BATTERY_ID = "BAT-900008"


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
        for battery_id in (TEST_BATTERY_ID, OTHER_BATTERY_ID):
            await session.execute(
                delete(BatteryCurrentState).where(BatteryCurrentState.battery_id == battery_id)
            )
            await session.execute(delete(Telemetry).where(Telemetry.battery_id == battery_id))
            await session.execute(delete(Battery).where(Battery.battery_id == battery_id))
        await session.commit()


async def _register_battery(battery_id: str) -> None:
    async with async_session_maker() as session:
        session.add(
            Battery(
                battery_id=battery_id,
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
    await _register_battery(TEST_BATTERY_ID)
    await _register_battery(OTHER_BATTERY_ID)
    yield
    await _cleanup()


async def _get_current_state(battery_id: str) -> BatteryCurrentState | None:
    async with async_session_maker() as session:
        return await session.get(BatteryCurrentState, battery_id)


async def test_registered_battery_with_no_telemetry_has_no_current_state_row():
    # Contract section 21: a registered battery that has never reported
    # telemetry MAY have no current-state row -- this proves the table
    # doesn't get a fabricated row just because a battery was registered.
    stored = await _get_current_state(TEST_BATTERY_ID)
    assert stored is None


async def test_single_ingestion_creates_a_current_state_row(client):
    payload = _valid_event_payload(state_of_charge=42.0, temperature_c=30.0, status="DISCHARGING")

    response = await client.post("/api/v1/telemetry", json=payload)
    assert response.status_code == 201

    stored = await _get_current_state(TEST_BATTERY_ID)
    assert stored is not None
    assert stored.state_of_charge == 42.0
    assert stored.temperature_c == 30.0
    assert stored.status == "DISCHARGING"
    assert stored.last_seen == datetime.fromisoformat(payload["timestamp"])


async def test_a_newer_single_event_advances_current_state(client):
    now = datetime.now(timezone.utc)
    first = _valid_event_payload(timestamp=now.isoformat(), state_of_charge=50.0)
    second = _valid_event_payload(
        timestamp=(now + timedelta(seconds=5)).isoformat(), state_of_charge=55.0
    )

    await client.post("/api/v1/telemetry", json=first)
    await client.post("/api/v1/telemetry", json=second)

    stored = await _get_current_state(TEST_BATTERY_ID)
    assert stored.state_of_charge == 55.0
    assert stored.last_seen == datetime.fromisoformat(second["timestamp"])


async def test_an_out_of_order_older_event_does_not_move_current_state_backwards(client):
    # Contract section 18: current state must never move backwards in time
    # just because a late/out-of-order event arrives. Both events are still
    # new (different event_ids), so both land in telemetry history -- only
    # the current-state *pointer* must stay on the newer one.
    now = datetime.now(timezone.utc)
    newer = _valid_event_payload(timestamp=now.isoformat(), state_of_charge=80.0)
    older = _valid_event_payload(
        timestamp=(now - timedelta(seconds=30)).isoformat(), state_of_charge=20.0
    )

    await client.post("/api/v1/telemetry", json=newer)
    response = await client.post("/api/v1/telemetry", json=older)
    assert response.status_code == 201  # a genuinely new historical row

    stored = await _get_current_state(TEST_BATTERY_ID)
    assert stored.state_of_charge == 80.0
    assert stored.last_seen == datetime.fromisoformat(newer["timestamp"])


async def test_duplicate_event_retry_does_not_touch_current_state(client):
    payload = _valid_event_payload(state_of_charge=33.0)
    await client.post("/api/v1/telemetry", json=payload)

    # Same event_id, different (impossible in practice, but proves the
    # point) values -- since this is a no-op duplicate, current_state must
    # still reflect the ORIGINAL values, matching how the historical row
    # itself is protected (Roadmap 1.8).
    retried = {**payload, "state_of_charge": 5.0}
    response = await client.post("/api/v1/telemetry", json=retried)
    assert response.status_code == 200

    stored = await _get_current_state(TEST_BATTERY_ID)
    assert stored.state_of_charge == 33.0


async def test_batch_ingestion_uses_the_newest_event_per_battery(client):
    now = datetime.now(timezone.utc)
    batch = {
        "events": [
            _valid_event_payload(timestamp=now.isoformat(), state_of_charge=40.0),
            _valid_event_payload(
                timestamp=(now + timedelta(seconds=10)).isoformat(), state_of_charge=45.0
            ),
            _valid_event_payload(
                timestamp=(now - timedelta(seconds=10)).isoformat(), state_of_charge=5.0
            ),
        ]
    }

    response = await client.post("/api/v1/telemetry/batch", json=batch)
    assert response.status_code == 201
    assert response.json()["inserted"] == 3

    stored = await _get_current_state(TEST_BATTERY_ID)
    assert stored.state_of_charge == 45.0
    assert stored.last_seen == datetime.fromisoformat(
        (now + timedelta(seconds=10)).isoformat()
    )


async def test_batch_spanning_two_batteries_updates_both_independently(client):
    now = datetime.now(timezone.utc)
    batch = {
        "events": [
            _valid_event_payload(
                battery_id=TEST_BATTERY_ID, timestamp=now.isoformat(), state_of_charge=61.0
            ),
            _valid_event_payload(
                battery_id=OTHER_BATTERY_ID, timestamp=now.isoformat(), state_of_charge=77.0
            ),
        ]
    }

    await client.post("/api/v1/telemetry/batch", json=batch)

    first_state = await _get_current_state(TEST_BATTERY_ID)
    second_state = await _get_current_state(OTHER_BATTERY_ID)
    assert first_state.state_of_charge == 61.0
    assert second_state.state_of_charge == 77.0


async def test_batch_of_only_duplicates_does_not_touch_current_state(client):
    payload = _valid_event_payload(state_of_charge=70.0)
    await client.post("/api/v1/telemetry", json=payload)

    # Resend the same event, now inside a batch -- it's a duplicate, so
    # nothing about current_state should change.
    duplicate_batch = {"events": [{**payload, "state_of_charge": 1.0}]}
    response = await client.post("/api/v1/telemetry/batch", json=duplicate_batch)
    assert response.status_code == 200
    assert response.json()["inserted"] == 0

    stored = await _get_current_state(TEST_BATTERY_ID)
    assert stored.state_of_charge == 70.0
