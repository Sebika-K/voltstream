"""Integration tests for `GET /api/v1/batteries/{battery_id}/telemetry`
(Roadmap 2.3, Contract section 31).
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

WITH_HISTORY_BATTERY_ID = "BAT-900020"
NO_HISTORY_BATTERY_ID = "BAT-900021"
UNKNOWN_BATTERY_ID = "BAT-900022"  # never registered

BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _make_event(battery_id: str, *, minutes_offset: int, state_of_charge: float) -> Telemetry:
    return Telemetry(
        event_id=uuid.uuid4(),
        battery_id=battery_id,
        timestamp=BASE_TIME + timedelta(minutes=minutes_offset),
        state_of_charge=state_of_charge,
        voltage=49.0,
        current=8.0,
        power_kw=1.0,
        temperature_c=25.0,
        health_percent=100.0,
        status="CHARGING",
    )


async def _cleanup() -> None:
    async with async_session_maker() as session:
        for battery_id in (WITH_HISTORY_BATTERY_ID, NO_HISTORY_BATTERY_ID):
            await session.execute(
                delete(BatteryCurrentState).where(BatteryCurrentState.battery_id == battery_id)
            )
            await session.execute(delete(Telemetry).where(Telemetry.battery_id == battery_id))
            await session.execute(delete(Battery).where(Battery.battery_id == battery_id))
        await session.commit()


async def _register(battery_id: str) -> None:
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
    await _register(WITH_HISTORY_BATTERY_ID)
    await _register(NO_HISTORY_BATTERY_ID)
    async with async_session_maker() as session:
        # Five readings, 10 minutes apart, inserted out of chronological
        # order on purpose -- a test asserting "newest first" is then
        # actually proving something about the query, not just echoing
        # insertion order.
        events = [
            _make_event(WITH_HISTORY_BATTERY_ID, minutes_offset=20, state_of_charge=50.0),
            _make_event(WITH_HISTORY_BATTERY_ID, minutes_offset=0, state_of_charge=10.0),
            _make_event(WITH_HISTORY_BATTERY_ID, minutes_offset=40, state_of_charge=70.0),
            _make_event(WITH_HISTORY_BATTERY_ID, minutes_offset=10, state_of_charge=30.0),
            _make_event(WITH_HISTORY_BATTERY_ID, minutes_offset=30, state_of_charge=60.0),
        ]
        for event in events:
            session.add(event)
        await session.commit()
    yield
    await _cleanup()


async def test_events_are_returned_newest_first(client):
    response = await client.get(f"/api/v1/batteries/{WITH_HISTORY_BATTERY_ID}/telemetry")
    assert response.status_code == 200

    socs = [event["state_of_charge"] for event in response.json()["events"]]
    assert socs == [70.0, 60.0, 50.0, 30.0, 10.0]  # minute 40 down to minute 0


async def test_start_and_end_filter_the_time_range(client):
    response = await client.get(
        f"/api/v1/batteries/{WITH_HISTORY_BATTERY_ID}/telemetry",
        params={
            "start": (BASE_TIME + timedelta(minutes=10)).isoformat(),
            "end": (BASE_TIME + timedelta(minutes=30)).isoformat(),
        },
    )
    socs = {event["state_of_charge"] for event in response.json()["events"]}
    assert socs == {30.0, 50.0, 60.0}  # minutes 10, 20, 30 -- inclusive bounds
    assert 10.0 not in socs  # minute 0, before start
    assert 70.0 not in socs  # minute 40, after end


async def test_limit_caps_results_to_the_newest_ones(client):
    response = await client.get(
        f"/api/v1/batteries/{WITH_HISTORY_BATTERY_ID}/telemetry", params={"limit": 2}
    )
    body = response.json()
    assert body["count"] == 2
    assert [event["state_of_charge"] for event in body["events"]] == [70.0, 60.0]


async def test_a_battery_with_no_history_returns_an_empty_list_not_404(client):
    response = await client.get(f"/api/v1/batteries/{NO_HISTORY_BATTERY_ID}/telemetry")
    assert response.status_code == 200
    body = response.json()
    assert body["events"] == []
    assert body["count"] == 0


async def test_unknown_battery_returns_404_with_error_envelope(client):
    response = await client.get(f"/api/v1/batteries/{UNKNOWN_BATTERY_ID}/telemetry")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "BATTERY_NOT_FOUND"


async def test_default_limit_and_resolution_are_applied_when_omitted(client):
    response = await client.get(f"/api/v1/batteries/{WITH_HISTORY_BATTERY_ID}/telemetry")
    body = response.json()
    assert body["limit"] == 100
    assert body["resolution"] == "raw"


async def test_limit_over_the_maximum_is_rejected_with_422(client):
    response = await client.get(
        f"/api/v1/batteries/{WITH_HISTORY_BATTERY_ID}/telemetry", params={"limit": 1001}
    )
    assert response.status_code == 422


async def test_unsupported_resolution_value_is_rejected_with_422(client):
    response = await client.get(
        f"/api/v1/batteries/{WITH_HISTORY_BATTERY_ID}/telemetry", params={"resolution": "hourly"}
    )
    assert response.status_code == 422


async def test_response_includes_full_event_fields(client):
    response = await client.get(
        f"/api/v1/batteries/{WITH_HISTORY_BATTERY_ID}/telemetry", params={"limit": 1}
    )
    event = response.json()["events"][0]
    assert set(event.keys()) == {
        "event_id",
        "battery_id",
        "timestamp",
        "state_of_charge",
        "voltage",
        "current",
        "power_kw",
        "temperature_c",
        "health_percent",
        "status",
    }
    assert event["battery_id"] == WITH_HISTORY_BATTERY_ID
