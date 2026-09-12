"""Single telemetry ingestion logic (Roadmap 1.7, Contract section 15).

Roadmap 1.7 is deliberately narrower than the Contract's full section 15
ingestion flow. This function does exactly what 1.7 asks for -- validate,
confirm the battery is registered, store the reading -- and nothing from the
later steps that flow depends on:

- Duplicate `event_id` handling ("ON CONFLICT DO NOTHING") is Roadmap 1.8,
  not this step. Right now, POSTing the same event twice will raise a
  database integrity error instead of succeeding idempotently -- that's a
  known, deliberate gap this function does not paper over, because 1.8 is
  the step that's supposed to close it.
- Updating a `battery_current_state` row is Roadmap 2.1 -- that table
  doesn't exist yet.
- Evaluating anomaly rules and publishing realtime updates are Phase 3.

This keeps each roadmap step doing one verifiable thing instead of a little
bit of everything at once.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import APIError
from app.models.battery import Battery
from app.models.telemetry import Telemetry
from app.schemas.telemetry import TelemetryEvent


async def ingest_telemetry_event(session: AsyncSession, event: TelemetryEvent) -> Telemetry:
    """Store one telemetry reading, after confirming its battery exists.

    Raises `APIError` (404) if `event.battery_id` isn't a registered battery --
    the Contract's "Telemetry for an unregistered battery MUST be rejected."
    """
    battery = await session.get(Battery, event.battery_id)
    if battery is None:
        raise APIError(
            status_code=404,
            code="BATTERY_NOT_FOUND",
            message=f"Battery {event.battery_id} was not found",
        )

    row = Telemetry(
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
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row
