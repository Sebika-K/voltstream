"""Integration tests for `GET /api/v1/fleet/summary` (Roadmap 2.4, Contract
section 28).

Every other test file so far has been able to scope its assertions down to
the specific battery IDs it created -- `test_battery_list.py`, for example,
checks membership rather than the whole table's totals, precisely because
other test files share the same `batteries` table. Fleet summary has no such
escape hatch: it is *defined* as an aggregate over the whole table, with no
filter parameters at all. So this file takes a different, deliberate
approach instead: it wipes the three telemetry-related tables clean before
each test and builds its own known dataset from nothing, per the Roadmap's
explicit instruction for this task ("use a known dataset and verify
calculations exactly"). That only works because the suite runs
single-threaded against one database -- it is not the pattern to copy for a
test file that needs to coexist with others mid-test.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest_asyncio
from sqlalchemy import delete

from app.db.session import async_session_maker
from app.models.battery import Battery
from app.models.battery_current_state import BatteryCurrentState
from app.models.telemetry import Telemetry


async def _wipe_fleet_tables() -> None:
    async with async_session_maker() as session:
        await session.execute(delete(BatteryCurrentState))
        await session.execute(delete(Telemetry))
        await session.execute(delete(Battery))
        await session.commit()


async def _register(battery_id: str, *, capacity_kwh: float) -> None:
    async with async_session_maker() as session:
        session.add(
            Battery(
                battery_id=battery_id,
                capacity_kwh=capacity_kwh,
                max_power_kw=5.0,
                nominal_voltage=48.0,
                profile_type="RESIDENTIAL",
                created_at=datetime.now(timezone.utc),
            )
        )
        await session.commit()


async def _set_current_state(battery_id: str, *, state_of_charge: float, status: str) -> None:
    async with async_session_maker() as session:
        session.add(
            BatteryCurrentState(
                battery_id=battery_id,
                last_seen=datetime.now(timezone.utc),
                state_of_charge=state_of_charge,
                temperature_c=25.0,
                power_kw=1.0,
                health_percent=100.0,
                status=status,
            )
        )
        await session.commit()


@pytest_asyncio.fixture(autouse=True)
async def _clean_slate():
    await _wipe_fleet_tables()
    yield
    await _wipe_fleet_tables()


async def test_empty_fleet_returns_all_zeroes_not_an_error(client):
    response = await client.get("/api/v1/fleet/summary")
    assert response.status_code == 200
    assert response.json() == {
        "total_devices": 0,
        "online_devices": 0,
        "offline_devices": 0,
        "average_soc": 0.0,
        "total_available_energy_kwh": 0.0,
        "charging_devices": 0,
        "discharging_devices": 0,
        "idle_devices": 0,
        "active_alerts": 0,
        "critical_alerts": 0,
    }


async def test_known_dataset_produces_exact_expected_totals(client):
    # A known, hand-computable fleet: 5 batteries, 4 with a current-state
    # row and 1 that has never reported telemetry at all.
    await _register("BAT-900030", capacity_kwh=10.0)
    await _set_current_state("BAT-900030", state_of_charge=80.0, status="CHARGING")

    await _register("BAT-900031", capacity_kwh=20.0)
    await _set_current_state("BAT-900031", state_of_charge=40.0, status="DISCHARGING")

    await _register("BAT-900032", capacity_kwh=5.0)
    await _set_current_state("BAT-900032", state_of_charge=100.0, status="IDLE")

    await _register("BAT-900033", capacity_kwh=8.0)
    await _set_current_state("BAT-900033", state_of_charge=0.0, status="FAULT")

    await _register("BAT-900034", capacity_kwh=15.0)
    # BAT-900034 deliberately gets no current_state row -- never reported.

    response = await client.get("/api/v1/fleet/summary")
    assert response.status_code == 200
    body = response.json()

    assert body["total_devices"] == 5
    # 4 batteries have ever reported (900030-900033); the 5th never has.
    assert body["online_devices"] == 4
    assert body["offline_devices"] == 1
    # (80 + 40 + 100 + 0) / 4 known-state batteries -- the never-reported
    # battery does not enter this average at all (Contract section 28).
    assert body["average_soc"] == 55.0
    # (10*0.80) + (20*0.40) + (5*1.00) + (8*0.00) = 8 + 8 + 5 + 0
    assert body["total_available_energy_kwh"] == 21.0
    assert body["charging_devices"] == 1
    assert body["discharging_devices"] == 1
    assert body["idle_devices"] == 1
    # BAT-900033 is FAULT -- it has known state (counted online, counted in
    # the average/energy totals) but belongs to none of the three named
    # status buckets the Contract asks for.
    assert body["active_alerts"] == 0
    assert body["critical_alerts"] == 0


async def test_offline_status_devices_still_count_toward_state_derived_averages(client):
    """A battery explicitly marked OFFLINE (Roadmap 3.2 now writes this status --
    the status value is already valid) still has a *known* current-state
    reading; it just doesn't count as currently online. It should still
    contribute to average_soc/available energy, per Contract section 28's
    "only batteries with known current telemetry state" rule -- an OFFLINE
    device meets that bar, a never-reported device doesn't."""
    await _register("BAT-900035", capacity_kwh=10.0)
    await _set_current_state("BAT-900035", state_of_charge=60.0, status="OFFLINE")

    response = await client.get("/api/v1/fleet/summary")
    body = response.json()

    assert body["total_devices"] == 1
    assert body["online_devices"] == 0
    assert body["offline_devices"] == 1
    assert body["average_soc"] == 60.0
    assert body["total_available_energy_kwh"] == 6.0
    assert body["charging_devices"] == 0
    assert body["discharging_devices"] == 0
    assert body["idle_devices"] == 0
