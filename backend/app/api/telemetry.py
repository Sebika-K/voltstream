"""Telemetry ingestion endpoints (Roadmap 1.7 + 1.8 + 1.9, Contract sections 15-17).

The Roadmap calls 1.7 "the first major project checkpoint": it's the first
time a reading travels the complete path -- generate it, validate it, check
the battery it claims to be from is real, store it in PostgreSQL. Roadmap 1.8
made that path retry-safe: sending the same event twice no longer errors.
Roadmap 1.9 adds a second entry point to the same storage logic: a batch of
many events in one request, which is how the simulator will actually talk to
this backend once it's wired up (Roadmap 1.10).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import APIError
from app.db.session import get_db_session
from app.schemas.telemetry import (
    TelemetryBatchRequest,
    TelemetryBatchResponse,
    TelemetryEvent,
    TelemetryIngestResponse,
)
from app.services.telemetry_service import ingest_telemetry_batch, ingest_telemetry_event

router = APIRouter(prefix="/api/v1/telemetry", tags=["telemetry"])


@router.post("", response_model=TelemetryIngestResponse)
async def create_telemetry_event(
    event: TelemetryEvent,
    response: Response,
    session: AsyncSession = Depends(get_db_session),
) -> TelemetryIngestResponse:
    """Ingest one telemetry event.

    A malformed body never reaches this function -- FastAPI validates it
    against `TelemetryEvent` first and returns 422 automatically. A body for
    a battery that isn't registered raises `APIError` (404) from the service
    layer. Otherwise: 201 if this event was newly stored, 200 if `event_id`
    was already known and this was a safe, no-op retry -- the same
    new-vs-already-exists status code pattern used by battery registration.
    """
    created = await ingest_telemetry_event(session, event)
    response.status_code = 201 if created else 200
    return TelemetryIngestResponse(event_id=event.event_id, battery_id=event.battery_id)


@router.post("/batch", response_model=TelemetryBatchResponse)
async def create_telemetry_batch(
    batch: TelemetryBatchRequest,
    response: Response,
    session: AsyncSession = Depends(get_db_session),
) -> TelemetryBatchResponse:
    """Ingest a batch of telemetry events in one request (Contract section 16).

    A structurally invalid event anywhere in the list never reaches this
    function -- FastAPI/Pydantic reject the whole request with 422 first,
    same as single ingestion, and nothing gets persisted. What this function
    does handle explicitly: a batch bigger than the configured maximum
    (413 -- a payload-size problem, not a malformed-data one), an
    unregistered battery_id anywhere in the batch (404, nothing persisted),
    and -- the actual point of this endpoint -- duplicate event_ids, which
    are NOT errors and simply get counted rather than rejected.

    Status code follows the same new-vs-nothing-new logic as single
    ingestion: 201 if at least one event in the batch was newly stored, 200
    if every event in the batch turned out to be a duplicate.
    """
    settings = get_settings()
    if len(batch.events) > settings.MAX_BATCH_SIZE:
        raise APIError(
            status_code=413,
            code="BATCH_TOO_LARGE",
            message=(
                f"Batch of {len(batch.events)} events exceeds the maximum of "
                f"{settings.MAX_BATCH_SIZE}"
            ),
        )

    inserted, duplicates = await ingest_telemetry_batch(session, batch)
    response.status_code = 201 if inserted > 0 else 200
    return TelemetryBatchResponse(
        received=len(batch.events), inserted=inserted, duplicates=duplicates
    )
