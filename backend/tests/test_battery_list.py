"""Integration tests for `GET /api/v1/batteries` (Roadmap 2.2, Contract
section 29).

This endpoint reads the whole `batteries` table, which other test files also
write to and clean up around. So these tests avoid asserting on raw
totals/counts from the full table -- instead they check membership and field
values for the specific batteries this file creates, except for the one test
that genuinely needs an exact, isolated count (pagination): that one filters
on a state_of_charge value distinctive enough that nothing else in the suite
is expected to use it.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest_asyncio
from sqlalchemy import delete

from app.db.session import async_session_maker
from app.models.battery import Battery
from app.models.battery_current_state import BatteryCurrentState
from app.models.telemetry import Telemetry

NO_TELEMETRY_BATTERY_ID = "BAT-900010"
CHARGING_BATTERY_ID = "BAT-900011"
DISCHARGING_BATTERY_ID = "BAT-900012"
PAGINATION_BATTERY_IDS = ["BAT-900013", "BAT-900014", "BAT-900015", "BAT-900016"]
ALL_TEST_BATTERY_IDS = [
    NO_TELEMETRY_BATTERY_ID,
    CHARGING_BATTERY_ID,
    DISCHARGING_BATTERY_ID,
    *PAGINATION_BATTERY_IDS,
]
PAGINATION_SOC = 12.34


async def _cleanup() -> None:
    async with async_session_maker() as session:
        for battery_id in ALL_TEST_BATTERY_IDS:
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
    await _cleanup()
    for battery_id in ALL_TEST_BATTERY_IDS:
        await _register(battery_id)
    await _set_current_state(CHARGING_BATTERY_ID, state_of_charge=80.0, status="CHARGING")
    await _set_current_state(DISCHARGING_BATTERY_ID, state_of_charge=20.0, status="DISCHARGING")
    for battery_id in PAGINATION_BATTERY_IDS:
        await _set_current_state(battery_id, state_of_charge=PAGINATION_SOC, status="CHARGING")
    # NO_TELEMETRY_BATTERY_ID deliberately gets no current_state row.
    yield
    await _cleanup()


def _by_id(items: list[dict], battery_id: str) -> dict | None:
    return next((item for item in items if item["battery_id"] == battery_id), None)


async def test_a_battery_with_no_telemetry_appears_with_null_current_state(client):
    response = await client.get("/api/v1/batteries", params={"limit": 500})
    assert response.status_code == 200

    item = _by_id(response.json()["batteries"], NO_TELEMETRY_BATTERY_ID)
    assert item is not None
    assert item["current_state"] is None


async def test_a_battery_with_current_state_shows_it_in_the_list(client):
    response = await client.get("/api/v1/batteries", params={"limit": 500})

    item = _by_id(response.json()["batteries"], CHARGING_BATTERY_ID)
    assert item is not None
    assert item["current_state"]["state_of_charge"] == 80.0
    assert item["current_state"]["status"] == "CHARGING"


async def test_status_filter_excludes_non_matching_batteries(client):
    response = await client.get(
        "/api/v1/batteries", params={"limit": 500, "status": "DISCHARGING"}
    )
    battery_ids = {item["battery_id"] for item in response.json()["batteries"]}

    assert DISCHARGING_BATTERY_ID in battery_ids
    assert CHARGING_BATTERY_ID not in battery_ids
    # A battery with no current-state row can never match a status filter.
    assert NO_TELEMETRY_BATTERY_ID not in battery_ids


async def test_min_soc_and_max_soc_filter_by_state_of_charge(client):
    response = await client.get(
        "/api/v1/batteries", params={"limit": 500, "min_soc": 50, "max_soc": 100}
    )
    battery_ids = {item["battery_id"] for item in response.json()["batteries"]}

    assert CHARGING_BATTERY_ID in battery_ids  # soc 80
    assert DISCHARGING_BATTERY_ID not in battery_ids  # soc 20
    assert NO_TELEMETRY_BATTERY_ID not in battery_ids  # no soc to compare at all


async def test_an_invalid_status_value_is_rejected_with_422(client):
    response = await client.get("/api/v1/batteries", params={"status": "NOT_A_REAL_STATUS"})
    assert response.status_code == 422


async def test_pagination_limit_and_offset_slice_correctly(client):
    filter_params = {"min_soc": PAGINATION_SOC, "max_soc": PAGINATION_SOC, "status": "CHARGING"}

    full = await client.get("/api/v1/batteries", params={**filter_params, "limit": 500})
    assert full.json()["total"] == len(PAGINATION_BATTERY_IDS)

    first_page = await client.get(
        "/api/v1/batteries", params={**filter_params, "limit": 2, "offset": 0}
    )
    second_page = await client.get(
        "/api/v1/batteries", params={**filter_params, "limit": 2, "offset": 2}
    )

    assert len(first_page.json()["batteries"]) == 2
    assert len(second_page.json()["batteries"]) == 2
    first_ids = {b["battery_id"] for b in first_page.json()["batteries"]}
    second_ids = {b["battery_id"] for b in second_page.json()["batteries"]}
    assert first_ids.isdisjoint(second_ids)
    assert first_ids | second_ids == set(PAGINATION_BATTERY_IDS)
    assert first_page.json()["total"] == len(PAGINATION_BATTERY_IDS)


async def test_limit_over_the_maximum_is_rejected_with_422(client):
    response = await client.get("/api/v1/batteries", params={"limit": 501})
    assert response.status_code == 422


async def test_default_limit_and_offset_are_applied_when_omitted(client):
    response = await client.get("/api/v1/batteries")
    body = response.json()
    assert body["limit"] == 100
    assert body["offset"] == 0
