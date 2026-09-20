"""Integration tests for `GET /api/v1/batteries/{battery_id}/prediction`
(Roadmap 4.5, Contract section 47).

Same seeding style as `tests/test_battery_detail.py`: real Postgres rows
via `async_session_maker`, then a real HTTP request through the `client`
fixture -- this endpoint's whole job is wiring together pieces (current
state, telemetry history, `compute_prediction`) that already have their
own focused unit tests (`test_prediction_baseline.py`,
`test_live_features.py`, `test_ml_prediction.py`), so what matters here is
that the wiring itself is correct, not re-proving the math.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest_asyncio
from sqlalchemy import delete

from app.db.session import async_session_maker
from app.models.battery import Battery
from app.models.battery_current_state import BatteryCurrentState
from app.models.telemetry import Telemetry
from app.services import model_registry

DISCHARGING_BATTERY_ID = "BAT-900030"
CHARGING_BATTERY_ID = "BAT-900031"
NO_STATE_BATTERY_ID = "BAT-900032"
UNKNOWN_BATTERY_ID = "BAT-900033"  # never registered


def _telemetry_row(battery_id: str, minutes_ago: float, *, soc: float, power_kw: float, status: str, now: datetime):
    return Telemetry(
        event_id=uuid.uuid4(),
        battery_id=battery_id,
        timestamp=now - timedelta(minutes=minutes_ago),
        state_of_charge=soc,
        voltage=48.0,
        current=10.0,
        power_kw=power_kw,
        temperature_c=27.0,
        health_percent=97.0,
        status=status,
    )


async def _cleanup() -> None:
    async with async_session_maker() as session:
        for battery_id in (DISCHARGING_BATTERY_ID, CHARGING_BATTERY_ID, NO_STATE_BATTERY_ID):
            await session.execute(
                delete(BatteryCurrentState).where(BatteryCurrentState.battery_id == battery_id)
            )
            await session.execute(delete(Telemetry).where(Telemetry.battery_id == battery_id))
            await session.execute(delete(Battery).where(Battery.battery_id == battery_id))
        await session.commit()


def _battery(battery_id: str) -> Battery:
    return Battery(
        battery_id=battery_id,
        capacity_kwh=10.0,
        max_power_kw=5.0,
        nominal_voltage=48.0,
        installation_date=date(2026, 1, 1),
        profile_type="RESIDENTIAL",
        created_at=datetime.now(timezone.utc),
    )


@pytest_asyncio.fixture(autouse=True)
async def _clean_slate():
    await _cleanup()
    model_registry.set_loaded_model(None)  # start every test with no ML model loaded
    now = datetime.now(timezone.utc)

    async with async_session_maker() as session:
        session.add_all(
            [
                _battery(DISCHARGING_BATTERY_ID),
                _battery(CHARGING_BATTERY_ID),
                _battery(NO_STATE_BATTERY_ID),
            ]
        )
        await session.commit()

        # A discharging battery with real telemetry history reaching back
        # more than 5 minutes -- enough for both the baseline AND (when a
        # model is loaded) the ML feature window to work.
        for minutes_ago, soc, power in [(8, 62.0, -2.0), (6, 60.0, -2.0), (3, 57.0, -2.0)]:
            session.add(
                _telemetry_row(
                    DISCHARGING_BATTERY_ID,
                    minutes_ago,
                    soc=soc,
                    power_kw=power,
                    status="DISCHARGING",
                    now=now,
                )
            )
        session.add(
            BatteryCurrentState(
                battery_id=DISCHARGING_BATTERY_ID,
                last_seen=now,
                state_of_charge=55.0,
                temperature_c=27.0,
                power_kw=-2.0,
                health_percent=97.0,
                status="DISCHARGING",
            )
        )

        session.add(
            BatteryCurrentState(
                battery_id=CHARGING_BATTERY_ID,
                last_seen=now,
                state_of_charge=70.0,
                temperature_c=25.0,
                power_kw=3.0,
                health_percent=99.0,
                status="CHARGING",
            )
        )
        await session.commit()

    yield
    await _cleanup()
    model_registry.set_loaded_model(None)


async def test_unknown_battery_returns_404(client):
    response = await client.get(f"/api/v1/batteries/{UNKNOWN_BATTERY_ID}/prediction")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "BATTERY_NOT_FOUND"


async def test_no_current_state_is_a_valid_unavailable_response_not_an_error(client):
    response = await client.get(f"/api/v1/batteries/{NO_STATE_BATTERY_ID}/prediction")
    assert response.status_code == 200
    body = response.json()
    assert body["battery_id"] == NO_STATE_BATTERY_ID
    assert body["current_soc"] is None
    assert body["critical_soc"] == 20.0
    assert body["prediction_available"] is False
    assert body["reason"] == "no_current_state"
    assert body["predicted_minutes_to_critical"] is None
    assert body["prediction_method"] is None


async def test_charging_battery_is_unavailable_with_the_right_reason(client):
    response = await client.get(f"/api/v1/batteries/{CHARGING_BATTERY_ID}/prediction")
    assert response.status_code == 200
    body = response.json()
    assert body["prediction_available"] is False
    assert body["reason"] == "battery_not_discharging"


async def test_discharging_battery_with_no_model_loaded_gets_a_real_baseline_prediction(client):
    response = await client.get(f"/api/v1/batteries/{DISCHARGING_BATTERY_ID}/prediction")
    assert response.status_code == 200
    body = response.json()

    assert body["battery_id"] == DISCHARGING_BATTERY_ID
    assert body["current_soc"] == 55.0
    assert body["critical_soc"] == 20.0
    assert body["prediction_available"] is True
    assert body["prediction_method"] == "baseline"
    assert body["model_version"] is None
    # Hand-computed (Contract 38): capacity(10) * ((55-20)/100) / abs(-2) * 60
    assert body["predicted_minutes_to_critical"] == 105.0
    assert body["predicted_critical_timestamp"] is not None


async def test_discharging_battery_with_a_loaded_model_gets_an_ml_prediction(client):
    class _FakePipeline:
        def predict(self, dataframe):
            return [42.0]

    model_registry.set_loaded_model(
        model_registry.LoadedModel(pipeline=_FakePipeline(), metadata={"model_version": "v1"})
    )

    response = await client.get(f"/api/v1/batteries/{DISCHARGING_BATTERY_ID}/prediction")
    assert response.status_code == 200
    body = response.json()

    assert body["prediction_available"] is True
    assert body["prediction_method"] == "ml"
    assert body["model_version"] == "v1"
    assert body["predicted_minutes_to_critical"] == 42.0
