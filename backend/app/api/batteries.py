"""Battery endpoints: registration (Roadmap 1.6, Contract section 6) and the
list/detail read APIs (Roadmap 2.2, Contract sections 29-30).

Registration was the first endpoint in the project that writes to the
database from an actual HTTP request. 2.2 is the first pair of endpoints
that *read* it back -- everything before this only ever wrote data in, on
the assumption that a future dashboard or client would eventually need to
see it. This is that "eventually."
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.schemas.battery import (
    BatteryCurrentStateResponse,
    BatteryDetailResponse,
    BatteryListItem,
    BatteryListResponse,
    BatteryRegistrationRequest,
    BatteryResponse,
)
from app.services.battery_service import get_battery_detail, list_batteries, register_battery

router = APIRouter(prefix="/api/v1/batteries", tags=["batteries"])

# Contract section 7 defines CHARGING/DISCHARGING/IDLE/FAULT as what a device
# reports; OFFLINE is explicitly called out there as a backend-derived state
# instead (Roadmap 3.2, not built yet). Both belong here: this is what a
# `battery_current_state.status` column can hold, not just what a producer
# sends, and filtering by ?status=OFFLINE should already be valid query
# syntax even before anything ever sets that value.
BatteryStatusFilter = Literal["CHARGING", "DISCHARGING", "IDLE", "FAULT", "OFFLINE"]


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


def _current_state_response(
    current_state,
) -> BatteryCurrentStateResponse | None:
    return BatteryCurrentStateResponse.model_validate(current_state) if current_state else None


@router.get("", response_model=BatteryListResponse)
async def list_batteries_endpoint(
    limit: int = Query(default=100, ge=1, le=500, description="Contract section 29: max 500"),
    offset: int = Query(default=0, ge=0),
    status: BatteryStatusFilter | None = Query(default=None),
    min_soc: float | None = Query(default=None, ge=0, le=100),
    max_soc: float | None = Query(default=None, ge=0, le=100),
    session: AsyncSession = Depends(get_db_session),
) -> BatteryListResponse:
    """List registered batteries with their current state.

    A battery that has never reported telemetry still appears in the plain
    (unfiltered) list -- it simply has `current_state: null` -- but is
    excluded the moment any of `status`/`min_soc`/`max_soc` is given, since
    there's nothing on record for it to match against (Contract section 21).
    """
    rows, total = await list_batteries(
        session,
        limit=limit,
        offset=offset,
        status=status,
        min_soc=min_soc,
        max_soc=max_soc,
    )
    batteries = [
        BatteryListItem(
            battery_id=battery.battery_id,
            capacity_kwh=battery.capacity_kwh,
            max_power_kw=battery.max_power_kw,
            nominal_voltage=battery.nominal_voltage,
            latitude=battery.latitude,
            longitude=battery.longitude,
            installation_date=battery.installation_date,
            profile_type=battery.profile_type,
            created_at=battery.created_at,
            current_state=_current_state_response(current_state),
        )
        for battery, current_state in rows
    ]
    return BatteryListResponse(batteries=batteries, total=total, limit=limit, offset=offset)


@router.get("/{battery_id}", response_model=BatteryDetailResponse)
async def get_battery_endpoint(
    battery_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> BatteryDetailResponse:
    """Fetch one battery's metadata and current state.

    Unknown `battery_id` -> `APIError` (404) from the service layer, which
    the registered exception handler turns into the Contract's error
    envelope before this function's return type ever matters. `prediction`
    and `alerts` are always `null` / `[]` for now -- see `BatteryDetailResponse`
    for why that's a deliberate placeholder, not an oversight.
    """
    battery, current_state = await get_battery_detail(session, battery_id)
    return BatteryDetailResponse(
        battery_id=battery.battery_id,
        capacity_kwh=battery.capacity_kwh,
        max_power_kw=battery.max_power_kw,
        nominal_voltage=battery.nominal_voltage,
        latitude=battery.latitude,
        longitude=battery.longitude,
        installation_date=battery.installation_date,
        profile_type=battery.profile_type,
        created_at=battery.created_at,
        current_state=_current_state_response(current_state),
        prediction=None,
        alerts=[],
    )
