"""The telemetry event contract (Implementation Contract section 7).

This is the shape every telemetry event must satisfy before anything else happens to
it. The simulator will eventually build JSON that matches this shape and POST it;
FastAPI will use this exact model to validate what it receives on the way in. Keeping
the contract in one place (rather than re-describing validation on both ends) is the
point of calling it a "contract."
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

# Contract section 4.1: battery IDs are BAT-###### (six digits), e.g. BAT-000421.
BATTERY_ID_PATTERN = re.compile(r"^BAT-\d{6}$")

# Contract section 7: "Electrical and temperature values MUST pass configured
# plausible input bounds" -- but the Contract doesn't hand us actual numbers for
# those bounds. These are reasonable, documented defaults of our own, deliberately
# generous: they exist to catch garbage/sensor-error data (NaN-ish, wildly
# out-of-physical-range numbers), NOT to enforce any specific battery's real limits.
# A battery's *actual* power ceiling is its own max_power_kw, checked elsewhere
# (against the registered battery), not here in the general-purpose schema.
MIN_PLAUSIBLE_VOLTAGE = 0.0
MAX_PLAUSIBLE_VOLTAGE = 1000.0
MIN_PLAUSIBLE_CURRENT_A = -10_000.0
MAX_PLAUSIBLE_CURRENT_A = 10_000.0
MIN_PLAUSIBLE_POWER_KW = -1_000.0
MAX_PLAUSIBLE_POWER_KW = 1_000.0
MIN_PLAUSIBLE_TEMPERATURE_C = -40.0
MAX_PLAUSIBLE_TEMPERATURE_C = 100.0

# Contract section 7: OFFLINE is deliberately excluded -- it's a backend-derived
# state from *missing* telemetry, never a value a producer sends.
TelemetryStatus = Literal["CHARGING", "DISCHARGING", "IDLE", "FAULT"]


class TelemetryEvent(BaseModel):
    """One telemetry reading from one battery, at one point in time."""

    event_id: UUID = Field(..., description="UUID v4 -- the idempotency key for this event")
    battery_id: str = Field(..., description="Must match BAT-###### (Contract section 4.1)")
    timestamp: datetime = Field(..., description="Must carry timezone info (Contract section 5)")
    state_of_charge: float = Field(..., ge=0, le=100)
    voltage: float = Field(..., ge=MIN_PLAUSIBLE_VOLTAGE, le=MAX_PLAUSIBLE_VOLTAGE)
    current: float = Field(..., ge=MIN_PLAUSIBLE_CURRENT_A, le=MAX_PLAUSIBLE_CURRENT_A)
    power_kw: float = Field(..., ge=MIN_PLAUSIBLE_POWER_KW, le=MAX_PLAUSIBLE_POWER_KW)
    temperature_c: float = Field(
        ..., ge=MIN_PLAUSIBLE_TEMPERATURE_C, le=MAX_PLAUSIBLE_TEMPERATURE_C
    )
    health_percent: float = Field(..., ge=0, le=100)
    status: TelemetryStatus

    @field_validator("battery_id")
    @classmethod
    def battery_id_must_match_format(cls, value: str) -> str:
        if not BATTERY_ID_PATTERN.match(value):
            raise ValueError(f"battery_id must match BAT-###### (got {value!r})")
        return value

    @field_validator("event_id")
    @classmethod
    def event_id_must_be_uuid_v4(cls, value: UUID) -> UUID:
        if value.version != 4:
            raise ValueError(f"event_id must be UUID v4 (got version {value.version})")
        return value

    @field_validator("timestamp")
    @classmethod
    def timestamp_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include timezone information")
        return value
