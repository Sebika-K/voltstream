"""Battery registration logic (Roadmap 1.6, Contract section 6).

Kept separate from `app/api/batteries.py` on purpose: the router's job is
"translate HTTP into a call here, translate the result back into HTTP"; this
module's job is the actual registration rule -- new battery, matching
re-registration, or conflicting re-registration -- with no FastAPI or HTTP
status codes anywhere in it. That split is what lets this logic be tested (or
reused, e.g. by a future admin script) without spinning up the web layer.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import APIError
from app.models.battery import Battery
from app.models.battery_current_state import BatteryCurrentState
from app.schemas.battery import BatteryRegistrationRequest

logger = logging.getLogger(__name__)

# The registration fields the Contract calls "immutable battery
# identity/configuration" -- everything from the request except battery_id
# itself (which is the identity, not part of what's being compared).
_IMMUTABLE_FIELDS = (
    "capacity_kwh",
    "max_power_kw",
    "nominal_voltage",
    "latitude",
    "longitude",
    "installation_date",
    "profile_type",
)


def _matches_existing(existing: Battery, request: BatteryRegistrationRequest) -> bool:
    """True if every immutable field on `request` exactly matches `existing`."""
    return all(
        getattr(existing, field) == getattr(request, field) for field in _IMMUTABLE_FIELDS
    )


async def register_battery(
    session: AsyncSession, request: BatteryRegistrationRequest
) -> tuple[Battery, bool]:
    """Register a battery, per the Contract's three-way registration behavior.

    Returns `(battery, created)`: `created` is True for a brand-new battery,
    False for a successful idempotent re-registration (identical metadata).
    Raises `APIError` (409) if `battery_id` already exists with *different*
    metadata -- the Contract's "MUST NOT silently modify immutable battery
    identity/configuration."
    """
    existing = await session.get(Battery, request.battery_id)

    if existing is not None:
        if _matches_existing(existing, request):
            # DEBUG, not INFO: every simulator restart re-registers the whole fleet,
            # and 100 identical "nothing changed" lines would bury the useful ones.
            logger.debug("battery_registration_repeated", extra={"battery_id": request.battery_id})
            return existing, False
        raise APIError(
            status_code=409,
            code="BATTERY_CONFIG_CONFLICT",
            message=(
                f"Battery {request.battery_id} is already registered with "
                "different configuration"
            ),
        )

    battery = Battery(
        battery_id=request.battery_id,
        capacity_kwh=request.capacity_kwh,
        max_power_kw=request.max_power_kw,
        nominal_voltage=request.nominal_voltage,
        latitude=request.latitude,
        longitude=request.longitude,
        installation_date=request.installation_date,
        profile_type=request.profile_type,
        created_at=datetime.now(timezone.utc),
    )
    session.add(battery)
    await session.commit()
    await session.refresh(battery)
    logger.info(
        "battery_registered",
        extra={"battery_id": battery.battery_id, "profile_type": str(battery.profile_type)},
    )
    return battery, True


async def list_batteries(
    session: AsyncSession,
    *,
    limit: int,
    offset: int,
    status: str | None = None,
    min_soc: float | None = None,
    max_soc: float | None = None,
) -> tuple[list[tuple[Battery, BatteryCurrentState | None]], int]:
    """List registered batteries with their current state (Roadmap 2.2,
    Contract section 29), filtered and paginated.

    `status`/`min_soc`/`max_soc` filter on `battery_current_state`, not on
    `Battery` itself (Contract section 28: "fleet calculations SHOULD
    primarily use battery_current_state"). A battery with no current-state
    row yet (Contract section 21) has nothing to compare those filters
    against, so it's excluded whenever any filter is given -- but it still
    appears in a plain, unfiltered list. `status` deliberately isn't
    restricted to a fixed set of values in this function: the column
    currently only ever holds what a device reported (CHARGING/DISCHARGING/
    IDLE/FAULT), but a backend-derived OFFLINE state (Roadmap 3.2, not built
    yet) is a real value this same column will eventually hold, and this
    filter should keep working unchanged once that lands.

    Returns `(rows, total)`: `rows` is this page's `(battery, current_state)`
    pairs -- `current_state` is `None` for a battery that has none --
    already ordered and sliced by `limit`/`offset`. `total` is the count of
    every battery matching the filters, ignoring pagination -- what a client
    needs to render "page 2 of N" without firing a second, hand-built query
    itself.
    """
    base_query = select(Battery, BatteryCurrentState).outerjoin(
        BatteryCurrentState, Battery.battery_id == BatteryCurrentState.battery_id
    )

    filters = []
    if status is not None:
        filters.append(BatteryCurrentState.status == status)
    if min_soc is not None:
        filters.append(BatteryCurrentState.state_of_charge >= min_soc)
    if max_soc is not None:
        filters.append(BatteryCurrentState.state_of_charge <= max_soc)
    if filters:
        base_query = base_query.where(*filters)

    total = await session.scalar(select(func.count()).select_from(base_query.subquery()))

    paginated_query = base_query.order_by(Battery.battery_id).limit(limit).offset(offset)
    result = await session.execute(paginated_query)
    rows = list(result.all())
    return rows, total or 0


async def get_battery_detail(
    session: AsyncSession, battery_id: str
) -> tuple[Battery, BatteryCurrentState | None]:
    """Fetch one battery plus its current state (Roadmap 2.2, Contract
    section 30).

    Raises `APIError` (404) if `battery_id` isn't registered at all -- the
    only thing that 404s here. A registered battery with no current-state
    row yet is a perfectly valid response (Contract section 21), not an
    error: the caller gets `current_state=None` and renders accordingly.
    """
    battery = await session.get(Battery, battery_id)
    if battery is None:
        raise APIError(
            status_code=404,
            code="BATTERY_NOT_FOUND",
            message=f"Battery {battery_id} was not found",
        )
    current_state = await session.get(BatteryCurrentState, battery_id)
    return battery, current_state
