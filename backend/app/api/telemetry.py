"""Single telemetry ingestion endpoint (Roadmap 1.7, Contract section 15).

The Roadmap calls this "the first major project checkpoint": it's the first
time a reading travels the complete path -- generate it, validate it, check
the battery it claims to be from is real, store it in PostgreSQL -- with
nothing simulated or skipped along the way.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.schemas.telemetry import TelemetryEvent, TelemetryIngestResponse
from app.services.telemetry_service import ingest_telemetry_event

router = APIRouter(prefix="/api/v1/telemetry", tags=["telemetry"])


@router.post("", response_model=TelemetryIngestResponse, status_code=201)
async def create_telemetry_event(
    event: TelemetryEvent,
    session: AsyncSession = Depends(get_db_session),
) -> TelemetryIngestResponse:
    """Ingest one telemetry event.

    A malformed body never reaches this function at all -- FastAPI validates
    it against `TelemetryEvent` first and returns 422 automatically. A body
    for a battery that isn't registered raises `APIError` (404) from the
    service layer, which the shared handler turns into the Contract's error
    envelope. Anything else is stored, and this returns 201.
    """
    stored = await ingest_telemetry_event(session, event)
    return TelemetryIngestResponse(event_id=stored.event_id, battery_id=stored.battery_id)
