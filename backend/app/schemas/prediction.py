"""`GET /api/v1/batteries/{battery_id}/prediction`'s response shape
(Roadmap 4.5, Contract section 47).

The Contract's own example only shows the *successful* case
(`prediction_method="ml"`, a real number, a real timestamp). This adds
`prediction_available` and `reason` alongside it -- the same two fields
`BaselinePredictionResult` (Roadmap 4.1) already uses -- so the
"unavailable" case the Contract also requires ("returns a defined reason
rather than a fabricated numeric prediction") has an actual field to put
that reason in, instead of overloading `prediction_method` to mean two
different things.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class PredictionResponse(BaseModel):
    """A successful prediction has `prediction_available=True`,
    `reason=None`, and every other field populated. An unavailable one has
    `prediction_available=False`, a non-null `reason`, and
    `predicted_minutes_to_critical` / `predicted_critical_timestamp` /
    `prediction_method` / `model_version` all `None` -- never a fabricated
    number standing in for "we don't actually know."""

    battery_id: str
    current_soc: float | None
    critical_soc: float
    prediction_available: bool
    predicted_minutes_to_critical: float | None
    predicted_critical_timestamp: datetime | None
    prediction_method: str | None
    model_version: str | None
    reason: str | None
