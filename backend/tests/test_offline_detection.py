"""Tests for offline detection (Roadmap 3.2, Contract sections 22-23).

Unlike earlier Phase 2/3 test files, there is no REST endpoint to call
through `client` here for the core behavior -- offline detection is a
background process, not a request/response feature (Contract section 22:
"runs periodically outside the primary telemetry request path"). Most tests
below call `mark_stale_batteries_offline` directly instead, the same way
the periodic loop in `app/services/offline_detector.py` calls it.

Uses the same "wipe the fleet tables clean, build a known dataset" approach
as `test_fleet_summary.py`, for the same reason: `mark_stale_batteries_offline`
is a blanket `UPDATE` over the *whole* `battery_current_state` table, with no
per-test scoping possible, so leftover rows from a differently-ordered test
run would silently change these results.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest_asyncio
from sqlalchemy import delete

from app.core.config import get_settings
from app.db.session import async_session_maker
from app.models.battery import Battery
from app.models.battery_current_state import BatteryCurrentState
from app.models.telemetry import Telemetry
from app.services.offline_detection_service import mark_stale_batteries_offline


async def _wipe_fleet_tables() -> None:
    async with async_session_maker() as session:
        await session.execute(delete(BatteryCurrentState))
        await session.execute(delete(Telemetry))
        await session.execute(delete(Battery))
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


async def _set_current_state(battery_id: str, *, last_seen: datetime, status: str) -> None:
    async with async_session_maker() as session:
        session.add(
            BatteryCurrentState(
                battery_id=battery_id,
                last_seen=last_seen,
                state_of_charge=50.0,
                temperature_c=25.0,
                power_kw=1.0,
                health_percent=100.0,
                status=status,
            )
        )
        await session.commit()


async def _get_status(battery_id: str) -> str:
    async with async_session_maker() as session:
        state = await session.get(BatteryCurrentState, battery_id)
        assert state is not None
        return state.status


@pytest_asyncio.fixture(autouse=True)
async def _clean_slate():
    await _wipe_fleet_tables()
    yield
    await _wipe_fleet_tables()


async def test_recent_battery_is_left_alone():
    threshold = get_settings().OFFLINE_THRESHOLD_SECONDS
    await _register("BAT-900040")
    await _set_current_state(
        "BAT-900040",
        last_seen=datetime.now(timezone.utc) - timedelta(seconds=threshold / 2),
        status="DISCHARGING",
    )

    async with async_session_maker() as session:
        newly_offline = await mark_stale_batteries_offline(session)

    assert newly_offline == 0
    assert await _get_status("BAT-900040") == "DISCHARGING"


async def test_stale_battery_is_marked_offline():
    threshold = get_settings().OFFLINE_THRESHOLD_SECONDS
    await _register("BAT-900041")
    await _set_current_state(
        "BAT-900041",
        last_seen=datetime.now(timezone.utc) - timedelta(seconds=threshold * 10),
        status="DISCHARGING",
    )

    async with async_session_maker() as session:
        newly_offline = await mark_stale_batteries_offline(session)

    assert newly_offline == 1
    assert await _get_status("BAT-900041") == "OFFLINE"


async def test_already_offline_battery_is_not_recounted():
    """A battery already OFFLINE isn't re-written every tick -- this is what
    the `WHERE status != 'OFFLINE'` half of the filter (in
    `mark_stale_batteries_offline`) is for. Not directly observable from the
    status alone (it stays "OFFLINE" either way), so this checks the
    *count* the function reports instead: an already-offline battery must
    never be counted as "newly" marked offline again."""
    threshold = get_settings().OFFLINE_THRESHOLD_SECONDS
    await _register("BAT-900042")
    await _set_current_state(
        "BAT-900042",
        last_seen=datetime.now(timezone.utc) - timedelta(seconds=threshold * 10),
        status="OFFLINE",
    )

    async with async_session_maker() as session:
        newly_offline = await mark_stale_batteries_offline(session)

    assert newly_offline == 0
    assert await _get_status("BAT-900042") == "OFFLINE"


async def test_battery_that_never_reported_telemetry_is_unaffected():
    """A registered battery with no `battery_current_state` row at all
    (Contract section 21) has nothing for this function to touch -- it
    must not error, and it must not fabricate a row."""
    await _register("BAT-900043")

    async with async_session_maker() as session:
        newly_offline = await mark_stale_batteries_offline(session)

    assert newly_offline == 0
    async with async_session_maker() as session:
        state = await session.get(BatteryCurrentState, "BAT-900043")
    assert state is None


async def test_mixed_fleet_only_flips_the_stale_ones():
    threshold = get_settings().OFFLINE_THRESHOLD_SECONDS
    await _register("BAT-900044")
    await _set_current_state(
        "BAT-900044",
        last_seen=datetime.now(timezone.utc) - timedelta(seconds=threshold / 2),
        status="CHARGING",
    )
    await _register("BAT-900045")
    await _set_current_state(
        "BAT-900045",
        last_seen=datetime.now(timezone.utc) - timedelta(seconds=threshold * 10),
        status="IDLE",
    )

    async with async_session_maker() as session:
        newly_offline = await mark_stale_batteries_offline(session)

    assert newly_offline == 1
    assert await _get_status("BAT-900044") == "CHARGING"
    assert await _get_status("BAT-900045") == "OFFLINE"


async def test_new_telemetry_automatically_recovers_an_offline_battery(client):
    """Recovery (Contract section 23) needs no new code of its own -- the
    existing telemetry-ingestion UPSERT (`app/services/telemetry_service.py`,
    Roadmap 2.1) already overwrites `status` unconditionally with whatever a
    newly-accepted event reports. This is a regression test for that
    interaction, not a test of anything new in this file -- it's here
    because Contract section 23 is part of what "offline detection" means
    even though the code for it already existed before 3.2."""
    await _register("BAT-900046")
    await _set_current_state(
        "BAT-900046",
        last_seen=datetime.now(timezone.utc) - timedelta(hours=1),
        status="OFFLINE",
    )

    response = await client.post(
        "/api/v1/telemetry",
        json={
            "event_id": str(uuid.uuid4()),
            "battery_id": "BAT-900046",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "state_of_charge": 70.0,
            "voltage": 49.0,
            "current": 5.0,
            "power_kw": 1.0,
            "temperature_c": 25.0,
            "health_percent": 100.0,
            "status": "CHARGING",
        },
    )

    assert response.status_code in (200, 201)
    assert await _get_status("BAT-900046") == "CHARGING"
