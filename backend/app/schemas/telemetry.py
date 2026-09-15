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

from pydantic import BaseModel, ConfigDict, Field, field_validator

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


class TelemetryIngestResponse(BaseModel):
    """What `POST /api/v1/telemetry` sends back on success.

    The Contract doesn't specify a response body for single ingestion (only
    the batch endpoint's `{"received", "inserted", "duplicates"}` shape is
    spelled out) -- this is a small, reasonable acknowledgment of our own: it
    confirms which event was accepted without repeating the whole payload
    back, since the caller already has that.
    """

    event_id: UUID
    battery_id: str
    status: Literal["accepted"] = "accepted"


class TelemetryBatchRequest(BaseModel):
    """Request body for `POST /api/v1/telemetry/batch` (Contract section 16).

    A batch is just "several `TelemetryEvent`s at once" -- reusing the exact same
    per-event validation means the batch endpoint enforces every rule the single
    endpoint does, for free. If any one event in the list fails that validation,
    FastAPI rejects the *entire* request with 422 before this model is even fully
    built, which is exactly the Contract's "structurally invalid -> whole request
    422, nothing persisted" rule -- no extra code needed to get that behavior.

    The maximum batch size (Contract: 1,000) is deliberately NOT enforced here as a
    Pydantic constraint: exceeding it is a payload-size problem, not a malformed-data
    problem, and the Contract lists a distinct status code (413) for that case. A
    schema-level max would collapse both cases into the same 422 response, so that
    size check happens explicitly in the endpoint instead (see app/api/telemetry.py).
    """

    events: list[TelemetryEvent] = Field(..., min_length=1)


class TelemetryBatchResponse(BaseModel):
    """What `POST /api/v1/telemetry/batch` sends back (Contract section 16, exact shape).

    `received` always equals `inserted + duplicates` -- every event in the batch
    lands in exactly one of those two buckets. "Duplicate" covers both an event_id
    repeated more than once inside this same request, and one that was already
    stored from some earlier request -- the Contract treats both the same way.
    """

    received: int
    inserted: int
    duplicates: int


class TelemetryHistoryItem(BaseModel):
    """One row of \`GET /api/v1/batteries/{battery_id}/telemetry\` (Roadmap
    2.3, Contract section 31).

    Unlike \`battery_current_state\`, history is about individual readings a
    client might trace back to a specific event -- so \`event_id\` is included
    here even though it isn't part of \`battery_current_state\`.
    """

    model_config = ConfigDict(from_attributes=True)

    event_id: UUID
    battery_id: str
    timestamp: datetime
    state_of_charge: float
    voltage: float
    current: float
    power_kw: float
    temperature_c: float
    health_percent: float
    status: str


class TelemetryHistoryResponse(BaseModel):
    """\`GET /api/v1/batteries/{battery_id}/telemetry\`'s response envelope.

    The Contract specifies this endpoint's query parameters (section 31) but
    not a response envelope. \`count\` is included for the same reason
    \`total\` was added to the battery list response in 2.2: \`limit\` alone
    doesn't tell a client whether it got everything that matched or was cut
    off by the Contract's required maximum-result-limit. \`resolution\`
    echoes back what was actually applied -- currently always \`"raw"\`, since
    time-bucketed aggregation is explicitly deferred by the Contract
    ("Aggregation through resolution MAY be implemented after raw history
    correctness is established") until a later step.
    """

    battery_id: str
    events: list[TelemetryHistoryItem]
    count: int
    limit: int
    resolution: str
