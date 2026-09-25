"""Battery endpoints: registration (Roadmap 1.6, Contract section 6), the
list/detail read APIs (Roadmap 2.2, Contract sections 29-30), and historical
telemetry (Roadmap 2.3, Contract section 31).

Registration was the first endpoint in the project that writes to the
database from an actual HTTP request. 2.2 was the first pair of endpoints
that *read* it back -- everything before that only ever wrote data in, on
the assumption that a future dashboard or client would eventually need to
see it. 2.3 extends that same idea from "what is this battery doing right
now" to "what has this battery done over a time range": still read-only,
just querying `telemetry`'s full history instead of `battery_current_state`'s
single latest row.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.metrics import PREDICTIONS
from app.db.session import get_db_session
from app.schemas.battery import (
    BatteryCurrentStateResponse,
    BatteryDetailResponse,
    BatteryListItem,
    BatteryListResponse,
    BatteryRegistrationRequest,
    BatteryResponse,
)
from app.schemas.prediction import PredictionResponse
from app.schemas.telemetry import TelemetryHistoryItem, TelemetryHistoryResponse
from app.services.battery_service import get_battery_detail, list_batteries, register_battery
from app.services.prediction_service import compute_prediction
from app.services.telemetry_service import get_battery_telemetry_history

# Roadmap 4.5: how far back to fetch telemetry for the ML feature window
# (Contract section 41's longest rolling window is 15 minutes). A little
# slack beyond exactly 15 minutes is intentional -- clock/query-boundary
# edges shouldn't cause a reading that's genuinely still relevant to be
# excluded.
_PREDICTION_HISTORY_LOOKBACK_MINUTES = 20
_PREDICTION_HISTORY_ROW_LIMIT = 2000

router = APIRouter(prefix="/api/v1/batteries", tags=["batteries"])

# Contract section 7 defines CHARGING/DISCHARGING/IDLE/FAULT as what a device
# reports; OFFLINE is explicitly called out there as a backend-derived state
# instead -- one that Roadmap 3.2's offline-detection loop now actually
# writes (see app/services/offline_detection_service.py). Both belong here:
# this is what a `battery_current_state.status` column can hold, not just
# what a producer sends, so filtering by ?status=OFFLINE is valid query
# syntax and now returns real, periodically-updated results.
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


@router.get("/{battery_id}/telemetry", response_model=TelemetryHistoryResponse)
async def get_battery_telemetry_endpoint(
    battery_id: str,
    start: datetime | None = Query(default=None, description="Inclusive lower timestamp bound"),
    end: datetime | None = Query(default=None, description="Inclusive upper timestamp bound"),
    limit: int = Query(default=100, ge=1, le=1000),
    resolution: Literal["raw"] = Query(
        default="raw",
        description=(
            "Only 'raw' is supported for now -- time-bucketed aggregation is "
            "deferred (Contract section 31 explicitly allows this)."
        ),
    ),
    session: AsyncSession = Depends(get_db_session),
) -> TelemetryHistoryResponse:
    """Fetch historical telemetry for one battery over an optional time range.

    Unknown `battery_id` -> `APIError` (404) from the service layer. A
    *registered* battery with no telemetry at all is not an error: it comes
    back as `events: []`, the same way a battery with no current-state row
    is a valid (not erroneous) detail response. `limit` is always enforced
    -- there is no way to ask for "everything."
    """
    events = await get_battery_telemetry_history(
        session, battery_id, start=start, end=end, limit=limit
    )
    return TelemetryHistoryResponse(
        battery_id=battery_id,
        events=[TelemetryHistoryItem.model_validate(event) for event in events],
        count=len(events),
        limit=limit,
        resolution=resolution,
    )


@router.get("/{battery_id}/prediction", response_model=PredictionResponse)
async def get_battery_prediction_endpoint(
    battery_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> PredictionResponse:
    """Depletion prediction for one battery (Roadmap 4.5, Contract section
    47): a real trained ML model if one is loaded and can actually answer,
    the physics baseline otherwise -- see
    `app/services/prediction_service.py`'s `compute_prediction` for the
    fallback chain itself (Contract section 46).

    Unknown `battery_id` -> `APIError` (404), same as every other
    per-battery endpoint. A registered battery with no current-state row
    yet is not an error -- every field this function can't answer just
    comes back `None`/unavailable, the same way `BatteryDetailResponse`
    already handles a battery that's never reported telemetry.
    """
    battery, current_state = await get_battery_detail(session, battery_id)
    settings = get_settings()

    if current_state is None:
        result = compute_prediction(
            capacity_kwh=battery.capacity_kwh,
            state_of_charge=None,
            power_kw=None,
            status=None,
            temperature_c=None,
            health_percent=None,
            profile_type=battery.profile_type,
            as_of=None,
            history=[],
            critical_soc_percent=settings.CRITICAL_SOC_PERCENT,
            min_discharge_power_kw=settings.MIN_DISCHARGE_POWER_KW,
        )
        PREDICTIONS.labels(method=result.prediction_method or "unavailable").inc()
        return PredictionResponse(
            battery_id=battery_id,
            current_soc=None,
            critical_soc=settings.CRITICAL_SOC_PERCENT,
            prediction_available=result.prediction_available,
            predicted_minutes_to_critical=result.predicted_minutes_to_critical,
            predicted_critical_timestamp=None,
            prediction_method=result.prediction_method,
            model_version=result.model_version,
            reason=result.reason,
        )

    # `as_of` is the current reading's own timestamp, not wall-clock "now"
    # -- the prediction is for this specific most-recent known state, and
    # computing "how much has SOC changed in the last 5 minutes" only
    # makes sense relative to when that state was actually observed, not
    # to whatever moment the HTTP request happens to arrive.
    as_of = current_state.last_seen
    telemetry_rows = await get_battery_telemetry_history(
        session,
        battery_id,
        start=as_of - timedelta(minutes=_PREDICTION_HISTORY_LOOKBACK_MINUTES),
        end=as_of,
        limit=_PREDICTION_HISTORY_ROW_LIMIT,
    )
    # Strictly BEFORE `as_of` -- the current reading itself is passed to
    # `compute_prediction` separately (from `current_state`), never
    # duplicated into the history list (see `live_features.py`'s own
    # comment on why that would double-count it).
    history = [
        (row.timestamp, row.state_of_charge, row.power_kw)
        for row in telemetry_rows
        if row.timestamp < as_of
    ]

    result = compute_prediction(
        capacity_kwh=battery.capacity_kwh,
        state_of_charge=current_state.state_of_charge,
        power_kw=current_state.power_kw,
        status=current_state.status,
        temperature_c=current_state.temperature_c,
        health_percent=current_state.health_percent,
        profile_type=battery.profile_type,
        as_of=as_of,
        history=history,
        critical_soc_percent=settings.CRITICAL_SOC_PERCENT,
        min_discharge_power_kw=settings.MIN_DISCHARGE_POWER_KW,
    )

    predicted_critical_timestamp = (
        as_of + timedelta(minutes=result.predicted_minutes_to_critical)
        if result.predicted_minutes_to_critical is not None
        else None
    )

    PREDICTIONS.labels(method=result.prediction_method or "unavailable").inc()
    return PredictionResponse(
        battery_id=battery_id,
        current_soc=current_state.state_of_charge,
        critical_soc=settings.CRITICAL_SOC_PERCENT,
        prediction_available=result.prediction_available,
        predicted_minutes_to_critical=result.predicted_minutes_to_critical,
        predicted_critical_timestamp=predicted_critical_timestamp,
        prediction_method=result.prediction_method,
        model_version=result.model_version,
        reason=result.reason,
    )
