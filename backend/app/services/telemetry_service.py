"""Single telemetry ingestion logic (Roadmap 1.7 + 1.8, Contract sections 15/17).

Roadmap 1.7 built the basic path: validate, confirm the battery is
registered, store the reading. This step (1.8) closes the one gap that was
left deliberately open -- what happens when the *same* event arrives twice
(a network retry, a simulator hiccup, anything). Before this change, that
raised a raw database integrity error. Now it's a safe no-op, per the
Contract's "ON CONFLICT (event_id) DO NOTHING": the second delivery of an
already-stored event succeeds without creating a duplicate row or touching
the row that's already there.

Still deliberately narrow: updating a `battery_current_state` row (Roadmap
2.1) and evaluating anomaly rules / publishing realtime updates (Phase 3)
are not part of this function.
"""

from __future__ import annotations

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import APIError
from app.models.battery import Battery
from app.models.telemetry import Telemetry
from app.schemas.telemetry import TelemetryEvent


async def ingest_telemetry_event(session: AsyncSession, event: TelemetryEvent) -> bool:
    """Store one telemetry reading, after confirming its battery exists.

    Returns `True` if this call actually created a new row, `False` if
    `event.event_id` already existed and this was a harmless no-op retry.
    Either way the caller ends up with the event safely persisted -- that's
    the point of "retry-safe, idempotent persistence" (Contract section 17).

    Raises `APIError` (404) if `event.battery_id` isn't a registered battery.
    """
    battery = await session.get(Battery, event.battery_id)
    if battery is None:
        raise APIError(
            status_code=404,
            code="BATTERY_NOT_FOUND",
            message=f"Battery {event.battery_id} was not found",
        )

    # `ON CONFLICT (event_id) DO NOTHING`: if a row with this event_id already
    # exists, PostgreSQL silently skips the insert -- it does NOT overwrite the
    # existing row with whatever this (possibly stale/retried) request
    # contains. `RETURNING event_id` only comes back for a row that was
    # actually inserted, which is exactly how we tell "new" from "duplicate"
    # without a separate lookup query first.
    insert_statement = (
        pg_insert(Telemetry)
        .values(
            event_id=event.event_id,
            battery_id=event.battery_id,
            timestamp=event.timestamp,
            state_of_charge=event.state_of_charge,
            voltage=event.voltage,
            current=event.current,
            power_kw=event.power_kw,
            temperature_c=event.temperature_c,
            health_percent=event.health_percent,
            status=event.status,
        )
        .on_conflict_do_nothing(index_elements=["event_id"])
        .returning(Telemetry.event_id)
    )
    result = await session.execute(insert_statement)
    created = result.scalar_one_or_none() is not None
    await session.commit()
    return created
