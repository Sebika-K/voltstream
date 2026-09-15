"""Periodic aggregated real-time publisher (Roadmap 3.1, Contract section 36).

The Contract is explicit that raw telemetry events must not each become a
browser message, and that operational UI updates should go out at roughly
1/second regardless of how many telemetry events arrived in between. This
loop is that aggregation point for fleet-wide state: instead of publishing
a `fleet_update` every time telemetry changes something (which could be
hundreds of times a second under load), it wakes up once a second, computes
one fresh fleet summary, and publishes exactly one event -- collapsing
however much happened in that second into a single UI-appropriate update.

It also skips the work entirely when nobody is connected
(`broadcaster.subscriber_count == 0`): no SSE clients means no reason to run
a database query every second just to throw the result away.

`battery_update`, `alert_created`, and `prediction_update` (the other event
types Roadmap 3.1 lists) aren't published from here yet. `battery_update`
belongs on the telemetry-ingestion path instead (Contract section 15's flow
ends with "publish aggregated realtime change" right after a battery's
current state is updated) and will get wired in when ingestion is next
touched; `alert_created`/`alert_resolved` and `prediction_update` don't have
anything to publish yet since alerts (3.3/3.4) and predictions (the ML
phase) don't exist in the system yet. The broadcaster and the wire format
already support any event type, so none of that requires touching this file
or `app/api/stream.py` again -- only adding a new `broadcaster.publish(...)`
call at the point where that feature computes its result.
"""

from __future__ import annotations

import asyncio
import logging

from app.db.session import async_session_maker
from app.services.broadcaster import broadcaster
from app.services.fleet_service import get_fleet_summary

logger = logging.getLogger(__name__)

# Contract section 36: "~1 update/second".
_PUBLISH_INTERVAL_SECONDS = 1.0


async def run_fleet_update_publisher() -> None:
    """Background loop: publish one `fleet_update` event per second.

    Started from `app/main.py`'s lifespan handler and cancelled on shutdown.
    Runs for the lifetime of the process; a single failed iteration (e.g. a
    momentary database hiccup) is logged and skipped rather than crashing
    the loop, since one missed update is harmless -- another one follows a
    second later.
    """
    while True:
        try:
            if broadcaster.subscriber_count > 0:
                async with async_session_maker() as session:
                    summary = await get_fleet_summary(session)
                broadcaster.publish("fleet_update", summary.model_dump())
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - one bad tick must not kill the loop
            logger.exception("fleet_update publisher iteration failed")
        await asyncio.sleep(_PUBLISH_INTERVAL_SECONDS)
