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


class BatteryCurrentStateResponse(BaseModel):
    """The `battery_current_state` fields, as exposed by the list and detail
    battery APIs (Roadmap 2.2).

    There is no "empty" instance of this model for a battery with no
    telemetry yet -- the *field* holding it (on `BatteryListItem` /
    `BatteryDetailResponse`) is `None` instead, matching Contract section
    21's "a registered battery that has never reported telemetry MAY have no
    current-state row."
    """

    model_config = ConfigDict(from_attributes=True)

    last_seen: datetime
    state_of_charge: float
    temperature_c: float
    power_kw: float
    health_percent: float
    status: str


class BatteryListItem(BaseModel):
    """One entry in `GET /api/v1/batteries` (Contract section 29)."""

    battery_id: str
    capacity_kwh: float
    max_power_kw: float
    nominal_voltage: float
    latitude: float | None
    longitude: float | None
    installation_date: date | None
    profile_type: str
    created_at: datetime
    current_state: BatteryCurrentStateResponse | None = None


class BatteryListResponse(BaseModel):
    """`GET /api/v1/batteries`'s response envelope.

    The Contract specifies this endpoint's query parameters (section 29) but
    not a response envelope -- `total` is a reasonable addition of our own:
    without it, a client paging through results with `limit`/`offset` has no
    way to know how many pages exist, or to render "showing 1-100 of 342."
    """

    batteries: list[BatteryListItem]
    total: int
    limit: int
    offset: int


class BatteryDetailResponse(BaseModel):
    """`GET /api/v1/batteries/{battery_id}`'s response (Contract section 30).

    `prediction` and `alerts` are part of the Contract's required response
    shape ("current state, if available", "latest prediction, if available",
    "unresolved alerts") -- but nothing populates them yet: predictions are
    Phase 4, alerts are Phase 3. They're included now, always `None` / `[]`,
    specifically so this response *shape* doesn't need to change again once
    those phases land -- only the service function filling them in will.
    """

    battery_id: str
    capacity_kwh: float
    max_power_kw: float
    nominal_voltage: float
    latitude: float | None
    longitude: float | None
    installation_date: date | None
    profile_type: str
    created_at: datetime
    current_state: BatteryCurrentStateResponse | None = None
    prediction: dict | None = None
    alerts: list[dict] = Field(default_factory=list)
