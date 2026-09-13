"""Single telemetry ingestion endpoint (Roadmap 1.7 + 1.8, Contract section 15/17).

The Roadmap calls 1.7 "the first major project checkpoint": it's the first
time a reading travels the complete path -- generate it, validate it, check
the battery it claims to be from is real, store it in PostgreSQL. Roadmap 1.8
made that path retry-safe: sending the same event twice no longer errors.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.schemas.telemetry import TelemetryEvent, TelemetryIngestResponse
from app.services.telemetry_service import ingest_telemetry_event

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
