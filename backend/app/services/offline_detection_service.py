"""Offline detection (Roadmap 3.2, Contract sections 22-23).

A battery's `battery_current_state.status` is only ever set by telemetry
ingestion (`app/services/telemetry_service.py`) -- to whatever status value
that reading itself carried. That's exactly right while telemetry keeps
arriving, but it means nothing ever notices a battery that simply stopped
sending telemetry altogether: its row just sits there, forever reporting
whatever status it last had (e.g. still "DISCHARGING" long after someone
stopped the simulator for it). This module is what notices that.

Recovery (Contract section 23) needs no code here at all: the next
telemetry event for a previously-offline battery goes through the same
`_upsert_current_state` UPSERT every event always does, which
unconditionally overwrites `status` with whatever that new reading reports
-- so a battery this module marks OFFLINE returns to a normal status
automatically, the moment it starts reporting again. Nothing here has to
"undo" the OFFLINE mark; ingestion already overwrites it for free.
"""

from __future__ import annotations

import datetime

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.battery_current_state import BatteryCurrentState

OFFLINE_STATUS = "OFFLINE"


async def mark_stale_batteries_offline(session: AsyncSession) -> list[str]:
    """Flip every battery whose `last_seen` is older than the configured
    threshold to `status="OFFLINE"` (Contract section 22).

    One bulk `UPDATE ... WHERE ...` rather than loading rows into Python and
    writing them back one at a time -- the same "let the database do the
    aggregate work" approach `get_fleet_summary` (Roadmap 2.4) already uses.
    The `WHERE status != 'OFFLINE'` half of the filter is what makes this
    safe to call repeatedly (every few seconds, per the background loop that
    calls it): a battery that's already marked OFFLINE is left alone rather
    than being re-written every tick for no reason. A battery with no
    `battery_current_state` row at all (never reported telemetry, Contract
    section 21) simply isn't matched by this `UPDATE` -- there's no row to
    flip, and none is fabricated.

    Returns the `battery_id`s newly marked offline this call (via a
    `RETURNING` clause on the same bulk `UPDATE` -- no second query) --
    empty on a perfectly normal, fully-online fleet. As of Roadmap 3.3, the
    caller (`app/services/offline_detector.py`) needs these ids, not just a
    count, to raise a `DEVICE_OFFLINE` alert for each one.
    """
    settings = get_settings()
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
        seconds=settings.OFFLINE_THRESHOLD_SECONDS
    )

    statement = (
        update(BatteryCurrentState)
        .where(BatteryCurrentState.status != OFFLINE_STATUS)
        .where(BatteryCurrentState.last_seen < cutoff)
        .values(status=OFFLINE_STATUS)
        .returning(BatteryCurrentState.battery_id)
    )
    result = await session.execute(statement)
    newly_offline_ids = list(result.scalars().all())
    await session.commit()
    return newly_offline_ids
