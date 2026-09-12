"""Battery registration endpoint (Roadmap 1.6, Contract section 6).

This is the first endpoint in the project that writes to the database from an
actual HTTP request -- everything before this (health/ready, the simulator,
the schemas) either read nothing or lived purely in memory.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.schemas.battery import BatteryRegistrationRequest, BatteryResponse
from app.services.battery_service import register_battery

router = APIRouter(prefix="/api/v1/batteries", tags=["batteries"])


@router.post("", response_model=BatteryResponse)
async def create_battery(
    payload: BatteryRegistrationRequest,
    response: Response,
    session: AsyncSession = Depends(get_db_session),
) -> BatteryResponse:
    """Register a battery.

    Status code depends on what actually happened, per the Contract:
    - 201 for a brand-new battery.
    - 200 for re-registering an already-known battery with identical metadata
      (an idempotent no-op, not an error).
    - 409 is raised as an `APIError` by the service layer for a battery_id that
      already exists with *different* metadata, and never reaches this
      function at all -- FastAPI's registered exception handler converts it
      straight into the Contract's error envelope.
    """
    battery, created = await register_battery(session, payload)
    response.status_code = 201 if created else 200
    return BatteryResponse.model_validate(battery)
