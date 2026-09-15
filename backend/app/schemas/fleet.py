"""The fleet summary response contract (Implementation Contract section 28).

Unlike every other response schema so far, this isn't built from a single
ORM row via `from_attributes` -- there's no `FleetSummary` table. It's a
computed rollup across every battery and every `battery_current_state` row,
assembled by `app/services/fleet_service.py` and handed to this model as
plain keyword arguments.
"""

from __future__ import annotations

from pydantic import BaseModel


class FleetSummaryResponse(BaseModel):
    """`GET /api/v1/fleet/summary`'s response. All ten fields are required by
    Contract section 28 -- none of them are optional or omitted-if-unknown,
    unlike `BatteryDetailResponse.prediction`/`alerts`. An empty fleet (no
    registered batteries) is a valid response too: every count is 0 and both
    averages are 0.0, not an error and not a null."""

    total_devices: int
    online_devices: int
    offline_devices: int
    average_soc: float
    total_available_energy_kwh: float
    charging_devices: int
    discharging_devices: int
    idle_devices: int
    active_alerts: int
    critical_alerts: int
