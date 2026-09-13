"""Telemetry ingestion logic -- single (Roadmap 1.7 + 1.8) and batch (Roadmap 1.9).

Contract sections 15/17 (single) and 16 (batch). Roadmap 1.7 built the basic path:
validate, confirm the battery is registered, store the reading. 1.8 made repeat
delivery of the same event safe (a no-op instead of a database error). 1.9 adds a
second way to reach the same storage: accept many events in one request instead of
one HTTP call per reading, which is how a real fleet simulator actually behaves.

Still deliberately narrow: updating a `battery_current_state` row (Roadmap 2.1) and
evaluating anomaly rules / publishing realtime updates (Phase 3) are not part of
either function here.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import APIError
from app.models.battery import Battery
from app.models.telemetry import Telemetry
from app.schemas.telemetry import TelemetryBatchRequest, TelemetryEvent


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


async def ingest_telemetry_batch(
    session: AsyncSession, batch: TelemetryBatchRequest
) -> tuple[int, int]:
    """Store a batch of telemetry readings in a single database transaction.

    Returns `(inserted, duplicates)` covering every event in the batch -- see
    `TelemetryBatchResponse` for exactly what those two numbers mean. Always
    `inserted + duplicates == len(batch.events)`.

    Raises `APIError` (404) if ANY event in the batch names a battery that
    isn't registered. This is checked up front, against the whole set of
    distinct battery_ids in the batch, before a single row is inserted --
    one query instead of one-per-event, and it means a single bad battery_id
    anywhere in the batch leaves the whole batch unpersisted (Contract
    section 19: historical insertion happens inside one transaction per
    request, so a failure before commit must roll back everything, not just
    the offending event).
    """
    battery_ids = {event.battery_id for event in batch.events}
    registered = await session.execute(
        select(Battery.battery_id).where(Battery.battery_id.in_(battery_ids))
    )
    registered_ids = set(registered.scalars().all())
    missing_ids = battery_ids - registered_ids
    if missing_ids:
        raise APIError(
            status_code=404,
            code="BATTERY_NOT_FOUND",
            message=f"Unknown battery_id(s): {', '.join(sorted(missing_ids))}",
        )

    # One multi-row INSERT for the whole batch, same ON CONFLICT DO NOTHING
    # idempotency rule as single ingestion. PostgreSQL processes the VALUES
    # rows in order: if the same event_id appears twice in this batch, the
    # first occurrence is inserted and the second is skipped as a conflict
    # against it -- so "duplicate within this request" and "duplicate
    # against something already stored" both come out the same way, exactly
    # as Contract section 16 requires ("duplicates occurring either inside
    # the request or against previously persisted events count as
    # duplicates"). RETURNING only gives back event_ids that were actually
    # inserted, so counting those rows is enough to derive both numbers.
    insert_statement = (
        pg_insert(Telemetry)
        .values(
            [
                {
                    "event_id": event.event_id,
                    "battery_id": event.battery_id,
                    "timestamp": event.timestamp,
                    "state_of_charge": event.state_of_charge,
                    "voltage": event.voltage,
                    "current": event.current,
                    "power_kw": event.power_kw,
                    "temperature_c": event.temperature_c,
                    "health_percent": event.health_percent,
                    "status": event.status,
                }
                for event in batch.events
            ]
        )
        .on_conflict_do_nothing(index_elements=["event_id"])
        .returning(Telemetry.event_id)
    )
    result = await session.execute(insert_statement)
    inserted = len(result.scalars().all())
    await session.commit()

    duplicates = len(batch.events) - inserted
    return inserted, duplicates
