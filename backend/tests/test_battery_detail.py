"""Integration tests for `GET /api/v1/batteries/{battery_id}` (Roadmap 2.2,
Contract section 30).
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest_asyncio
from sqlalchemy import delete

from app.db.session import async_session_maker
from app.models.battery import Battery
from app.models.battery_current_state import BatteryCurrentState
from app.models.telemetry import Telemetry

WITH_STATE_BATTERY_ID = "BAT-900017"
NO_STATE_BATTERY_ID = "BAT-900018"
UNKNOWN_BATTERY_ID = "BAT-900019"  # never registered


async def _cleanup() -> None:
    async with async_session_maker() as session:
        for battery_id in (WITH_STATE_BATTERY_ID, NO_STATE_BATTERY_ID):
            await session.execute(
                delete(BatteryCurrentState).where(BatteryCurrentState.battery_id == battery_id)
            )
            await session.execute(delete(Telemetry).where(Telemetry.battery_id == battery_id))
            await session.execute(delete(Battery).where(Battery.battery_id == battery_id))
        await session.commit()


@pytest_asyncio.fixture(autouse=True)
async def _clean_slate():
    await _cleanup()
    async with async_session_maker() as session:
        session.add(
            Battery(
                battery_id=WITH_STATE_BATTERY_ID,
                capacity_kwh=13.5,
                max_power_kw=5.0,
                nominal_voltage=48.0,
                latitude=37.7749,
                longitude=-122.4194,
                installation_date=date(2026, 1, 1),
                profile_type="SOLAR",
                created_at=datetime.now(timezone.utc),
            )
        )
        session.add(
            Battery(
                battery_id=NO_STATE_BATTERY_ID,
                capacity_kwh=10.0,
                max_power_kw=5.0,
                nominal_voltage=48.0,
                profile_type="RESIDENTIAL",
                created_at=datetime.now(timezone.utc),
            )
        )
        await session.commit()
        session.add(
            BatteryCurrentState(
                battery_id=WITH_STATE_BATTERY_ID,
                last_seen=datetime.now(timezone.utc),
                state_of_charge=64.0,
                temperature_c=28.0,
                power_kw=2.5,
                health_percent=97.0,
                status="DISCHARGING",
            )
        )
        await session.commit()
    yield
    await _cleanup()


async def test_detail_for_a_battery_with_current_state(client):
    response = await client.get(f"/api/v1/batteries/{WITH_STATE_BATTERY_ID}")
    assert response.status_code == 200

    body = response.json()
    assert body["battery_id"] == WITH_STATE_BATTERY_ID
    assert body["capacity_kwh"] == 13.5
    assert body["profile_type"] == "SOLAR"
    assert body["current_state"]["state_of_charge"] == 64.0
    assert body["current_state"]["status"] == "DISCHARGING"


async def test_detail_for_a_battery_with_no_current_state(client):
    response = await client.get(f"/api/v1/batteries/{NO_STATE_BATTERY_ID}")
    assert response.status_code == 200
    assert response.json()["current_state"] is None


async def test_prediction_and_alerts_are_always_present_but_empty_for_now(client):
    # Contract section 30 requires these keys in the response shape even
    # though nothing populates them yet (predictions are Phase 4, alerts are
    # Phase 3) -- pinning this placeholder shape means a future phase
    # changing it is a deliberate, visible decision, not an accidental
    # regression.
    response = await client.get(f"/api/v1/batteries/{WITH_STATE_BATTERY_ID}")
    body = response.json()
    assert body["prediction"] is None
    assert body["alerts"] == []


async def test_unknown_battery_returns_404_with_error_envelope(client):
    response = await client.get(f"/api/v1/batteries/{UNKNOWN_BATTERY_ID}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "BATTERY_NOT_FOUND"
