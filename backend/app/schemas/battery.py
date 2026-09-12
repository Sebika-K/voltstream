"""The battery registration contract (Implementation Contract section 6).

This describes what a client must send to `POST /api/v1/batteries`, and what
the API sends back. It reuses `BATTERY_ID_PATTERN` from the telemetry schema
(same file that already needed it for the same reason) rather than redefining
the regex a second place it could drift out of sync.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.telemetry import BATTERY_ID_PATTERN

# Contract section 6: the four supported profile types at registration time.
ProfileType = Literal["RESIDENTIAL", "SOLAR", "COMMERCIAL", "FAULTY"]


class BatteryRegistrationRequest(BaseModel):
    """The body of `POST /api/v1/batteries` -- everything the Contract lists as
    a required registration field."""

    battery_id: str = Field(..., description="Must match BAT-###### (Contract section 4.1)")
    capacity_kwh: float = Field(..., gt=0)
    max_power_kw: float = Field(..., gt=0)
    nominal_voltage: float = Field(..., gt=0)
    # The Contract lists latitude/longitude as registration fields but doesn't
    # specify bounds for them. -90..90 / -180..180 are just real-world
    # geographic limits, not a VoltStream-specific choice -- included so a
    # typo'd coordinate (e.g. 900.0) fails validation instead of being stored.
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    installation_date: date | None = None
    profile_type: ProfileType

    @field_validator("battery_id")
    @classmethod
    def battery_id_must_match_format(cls, value: str) -> str:
        if not BATTERY_ID_PATTERN.match(value):
            raise ValueError(f"battery_id must match BAT-###### (got {value!r})")
        return value


class BatteryResponse(BaseModel):
    """What `POST /api/v1/batteries` (and, later, any battery-reading endpoint)
    sends back -- built directly from a `Battery` ORM row via `from_attributes`,
    so there's no manual field-by-field copying to keep in sync."""

    model_config = ConfigDict(from_attributes=True)

    battery_id: str
    capacity_kwh: float
    max_power_kw: float
    nominal_voltage: float
    latitude: float | None
    longitude: float | None
    installation_date: date | None
    profile_type: str
    created_at: datetime
