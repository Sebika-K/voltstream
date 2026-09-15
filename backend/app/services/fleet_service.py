"""Fleet-wide analytics (Roadmap 2.4, Contract section 28).

Every other service function so far answers a question about one battery.
This one answers a question about the whole fleet at once: how many devices
are there, how many are online, what's the average charge level, how much
energy is available across everyone right now. It reads from
`battery_current_state`, not `telemetry`'s full history -- the Contract is
explicit that "fleet calculations SHOULD primarily use battery_current_state"
(the same reasoning that put battery list's status/SOC filters, Roadmap 2.2,
on that table too): it's the always-current, one-row-per-battery view, so
this is one cheap aggregate query instead of scanning every historical
reading a battery has ever sent.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.battery import Battery
from app.models.battery_current_state import BatteryCurrentState
from app.schemas.fleet import FleetSummaryResponse


async def get_fleet_summary(session: AsyncSession) -> FleetSummaryResponse:
    """Compute the fleet-wide summary (Contract section 28).

    `total_devices` counts every registered battery, full stop -- it is a
    fleet-size number, not a state-derived one. Everything else here depends
    on a battery actually having a `battery_current_state` row: the
    Contract is explicit that "only batteries with known current telemetry
    state contribute to state-derived averages," so a battery that has
    never reported telemetry contributes to `total_devices` and to
    `offline_devices` (it certainly isn't online) but to nothing else --
    it's invisible to `average_soc`, `total_available_energy_kwh`, and every
    status-bucket count.

    "Online" isn't spelled out in exact terms anywhere in the Contract or
    Roadmap, so this makes the same kind of reasonable, documented call that
    Roadmap 2.2 made for the OFFLINE status filter: a battery counts as
    online if it has a current-state row at all AND that row's status isn't
    explicitly `"OFFLINE"`. Nothing sets `status="OFFLINE"` yet -- that's
    Roadmap 3.2's job -- so right now `online_devices` is simply "batteries
    that have ever reported telemetry." Once 3.2 lands and starts marking
    stale batteries OFFLINE, this calculation picks that up automatically
    with no changes needed here. A battery explicitly marked OFFLINE still
    *has* known current state, though -- it still counts toward
    `average_soc` / `total_available_energy_kwh`, just not toward
    `online_devices`.
    """
    total_devices = await session.scalar(select(func.count()).select_from(Battery)) or 0

    state_query = (
        select(
            func.count().label("known_state_devices"),
            func.avg(BatteryCurrentState.state_of_charge).label("average_soc"),
            func.sum(Battery.capacity_kwh * BatteryCurrentState.state_of_charge / 100).label(
                "total_available_energy_kwh"
            ),
            func.count().filter(BatteryCurrentState.status == "CHARGING").label(
                "charging_devices"
            ),
            func.count().filter(BatteryCurrentState.status == "DISCHARGING").label(
                "discharging_devices"
            ),
            func.count().filter(BatteryCurrentState.status == "IDLE").label("idle_devices"),
            func.count().filter(BatteryCurrentState.status == "OFFLINE").label(
                "offline_status_devices"
            ),
        )
        .select_from(BatteryCurrentState)
        .join(Battery, Battery.battery_id == BatteryCurrentState.battery_id)
    )
    row = (await session.execute(state_query)).one()

    known_state_devices = row.known_state_devices or 0
    online_devices = known_state_devices - (row.offline_status_devices or 0)
    offline_devices = total_devices - online_devices

    return FleetSummaryResponse(
        total_devices=total_devices,
        online_devices=online_devices,
        offline_devices=offline_devices,
        # Rounded for display: the raw SQL average/sum can otherwise carry
        # long floating-point tails that have no real meaning at that
        # precision (e.g. an average across battery SOCs that don't divide
        # evenly).
        average_soc=round(row.average_soc, 2) if row.average_soc is not None else 0.0,
        total_available_energy_kwh=(
            round(row.total_available_energy_kwh, 4)
            if row.total_available_energy_kwh is not None
            else 0.0
        ),
        charging_devices=row.charging_devices or 0,
        discharging_devices=row.discharging_devices or 0,
        idle_devices=row.idle_devices or 0,
        # Phase 3 (alerts) doesn't exist yet -- always 0 for now, the same
        # future-proofing pattern as BatteryDetailResponse.alerts (Roadmap
        # 2.2): the field is already part of the response shape so nothing
        # here needs to change once alerts land.
        active_alerts=0,
        critical_alerts=0,
    )
