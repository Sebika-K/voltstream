"""Battery registration logic (Roadmap 1.6, Contract section 6).

Kept separate from `app/api/batteries.py` on purpose: the router's job is
"translate HTTP into a call here, translate the result back into HTTP"; this
module's job is the actual registration rule -- new battery, matching
re-registration, or conflicting re-registration -- with no FastAPI or HTTP
status codes anywhere in it. That split is what lets this logic be tested (or
reused, e.g. by a future admin script) without spinning up the web layer.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import APIError
from app.models.battery import Battery
from app.schemas.battery import BatteryRegistrationRequest

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
    return battery, True
