"""The alerts contract (Implementation Contract sections 24-27, TDD section
11's `GET /api/v1/alerts` / `PATCH /api/v1/alerts/{alert_id}`).

Roadmap 3.3 built the `alerts` table and the rules that fill it; this is
what finally lets a client (or Sebika's own browser, via curl) actually see
and act on what's in it.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict

# Contract section 24: the five supported alert types. Used as a query-filter
# type here (so `?alert_type=NOT_A_REAL_TYPE` is a 422, the same way
# `BatteryStatusFilter` in app/api/batteries.py validates `?status=`) rather
# than as the underlying column type -- the database column stays a plain
# VARCHAR, same reasoning as `app/models/alert.py`'s docstring.
AlertType = Literal[
    "LOW_SOC", "HIGH_TEMPERATURE", "RAPID_DISCHARGE", "VOLTAGE_ANOMALY", "DEVICE_OFFLINE"
]

# PRD FR-9 lists INFO/WARNING/CRITICAL as the full severity vocabulary, but
# `app/services/anomaly_detection_service.py` only ever produces WARNING or
# CRITICAL today -- INFO is left out of this filter type so an unused value
# can't silently "succeed" while matching nothing; it can be added back the
# moment something actually creates an INFO alert.
AlertSeverity = Literal["WARNING", "CRITICAL"]


class AlertResponse(BaseModel):
    """One alert row, as returned by both `GET /api/v1/alerts` and
    `PATCH /api/v1/alerts/{alert_id}` -- built directly from an `Alert` ORM
    row via `from_attributes`, same pattern as `BatteryResponse`."""

    model_config = ConfigDict(from_attributes=True)

    alert_id: uuid.UUID
    battery_id: str
    timestamp: dt.datetime
    alert_type: str
    severity: str
    message: str
    measured_value: float | None
    threshold_value: float | None
    resolved: bool
    resolved_at: dt.datetime | None


class AlertListResponse(BaseModel):
    """`GET /api/v1/alerts`'s response envelope -- same shape as
    `BatteryListResponse` (Roadmap 2.2): `total` is what lets a client render
    "showing 1-100 of N" without a second hand-built count query."""

    alerts: list[AlertResponse]
    total: int
    limit: int
    offset: int
