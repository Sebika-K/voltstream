"""Fleet-wide analytics endpoint (Roadmap 2.4, Contract section 28).

Every endpoint before this one describes a single battery: registration,
list, detail, or history. This is the first one that describes the fleet as
a whole -- one summary aggregated across every registered battery, so a
dashboard can answer "how is the fleet doing" without fetching every battery
individually and adding it up client-side.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.schemas.fleet import FleetSummaryResponse
from app.services.fleet_service import get_fleet_summary

router = APIRouter(prefix="/api/v1/fleet", tags=["fleet"])


@router.get("/summary", response_model=FleetSummaryResponse)
async def get_fleet_summary_endpoint(
    session: AsyncSession = Depends(get_db_session),
) -> FleetSummaryResponse:
    """Aggregate device counts, average SOC, and available energy across the
    whole fleet (Contract section 28).

    An empty fleet (no registered batteries at all) is a valid response --
    every count comes back 0 and both averages come back 0.0 -- not an
    error. See `app/services/fleet_service.py` for exactly which batteries
    count toward which field.
    """
    return await get_fleet_summary(session)
