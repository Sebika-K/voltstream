"""Concurrent ingestion must not deadlock (Roadmap 5.3 failure-test finding).

Every batch updates `battery_current_state` for many of the same batteries. If two
requests update those rows in a different order they can deadlock, PostgreSQL kills
one, and the caller gets a 500. The fix is to always update rows in sorted
`battery_id` order. Two tests cover it:

* a fast unit test that checks the order the rows are sent to the database in, and
* an integration test that fires many overlapping batches, in deliberately different
  orders, at the real database and expects every one to succeed.
"""

from __future__ import annotations

import asyncio
import random
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy import delete
from sqlalchemy.dialects import postgresql

from app.db.session import async_session_maker
from app.models.battery import Battery
from app.models.battery_current_state import BatteryCurrentState
from app.models.telemetry import Telemetry
from app.services.telemetry_service import _upsert_current_state

BATTERY_IDS = [f"BAT-9004{n:02d}" for n in range(12)]


def _event(battery_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        battery_id=battery_id,
        timestamp=datetime.now(timezone.utc),
        state_of_charge=55.0,
        temperature_c=26.0,
        power_kw=1.5,
        health_percent=99.0,
        status="CHARGING",
    )


class _CapturingSession:
    def __init__(self) -> None:
        self.statement = None

    async def execute(self, statement) -> None:
        self.statement = statement


async def test_current_state_rows_are_sent_to_the_database_in_sorted_order():
    events = [_event(b) for b in reversed(BATTERY_IDS)]  # deliberately reversed
    session = _CapturingSession()

    latest = await _upsert_current_state(session, events)

    assert list(latest) == sorted(BATTERY_IDS)
    params = session.statement.compile(dialect=postgresql.dialect()).params
    keys = sorted((k for k in params if k.startswith("battery_id_m")), key=lambda k: int(k[len("battery_id_m"):]))
    assert [params[k] for k in keys] == sorted(BATTERY_IDS)


async def _cleanup() -> None:
    async with async_session_maker() as session:
        await session.execute(delete(BatteryCurrentState).where(BatteryCurrentState.battery_id.in_(BATTERY_IDS)))
        await session.execute(delete(Telemetry).where(Telemetry.battery_id.in_(BATTERY_IDS)))
        await session.execute(delete(Battery).where(Battery.battery_id.in_(BATTERY_IDS)))
        await session.commit()


async def test_many_overlapping_batches_in_different_orders_all_succeed(client):
    await _cleanup()
    try:
        async with async_session_maker() as session:
            for battery_id in BATTERY_IDS:
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

        rng = random.Random(7)

        def batch() -> dict:
            ids = BATTERY_IDS[:]
            rng.shuffle(ids)  # every batch touches the same batteries in its own order
            return {
                "events": [
                    {
                        "event_id": str(uuid.uuid4()),
                        "battery_id": battery_id,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "state_of_charge": 55.0,
                        "voltage": 49.5,
                        "current": 12.3,
                        "power_kw": 1.5,
                        "temperature_c": 26.0,
                        "health_percent": 99.0,
                        "status": "CHARGING",
                    }
                    for battery_id in ids
                ]
            }

        responses = await asyncio.gather(
            *(client.post("/api/v1/telemetry/batch", json=batch()) for _ in range(12))
        )

        assert [r.status_code for r in responses] == [201] * 12
    finally:
        await _cleanup()
