"""Periodic offline-detection loop (Roadmap 3.2, Contract section 22:
"offline detection runs periodically outside the primary telemetry request
path").

Started from `app/main.py`'s lifespan alongside the Roadmap 3.1
`fleet_update` publisher -- a second, independent background task rather
than folded into the first one, since the two run on different natural
schedules (1/sec vs. every few seconds) and a failure in one has nothing to
do with the other.

Roadmap 3.3 adds one more responsibility here: every battery this loop just
flipped to OFFLINE gets a `DEVICE_OFFLINE` alert via the same
`create_or_retain_alert` helper the telemetry-triggered rules use
(`app/services/anomaly_detection_service.py`) -- this is the only place
that ever *creates* a `DEVICE_OFFLINE` alert; it is only ever *resolved*
from `app/services/telemetry_service.py`, the moment the battery reports
telemetry again.
"""

from __future__ import annotations

import asyncio
import datetime
import logging

from app.core.config import get_settings
from app.db.session import async_session_maker
from app.services.anomaly_detection_service import (
    ALERT_TYPE_DEVICE_OFFLINE,
    SEVERITY_WARNING,
    create_or_retain_alert,
)
from app.services.offline_detection_service import mark_stale_batteries_offline

logger = logging.getLogger(__name__)


async def run_offline_detection_loop() -> None:
    """Background loop: check for, and mark, newly-stale batteries, then
    raise a `DEVICE_OFFLINE` alert for each one.

    Runs for the lifetime of the process, started/cancelled from
    `app/main.py`'s lifespan handler. A single failed iteration (e.g. a
    momentary database hiccup) is logged and skipped rather than crashing
    the loop -- the next tick, a few seconds later, tries again.
    """
    settings = get_settings()
    while True:
        try:
            async with async_session_maker() as session:
                newly_offline_ids = await mark_stale_batteries_offline(session)
                if newly_offline_ids:
                    now = datetime.datetime.now(datetime.timezone.utc)
                    for battery_id in newly_offline_ids:
                        await create_or_retain_alert(
                            session,
                            battery_id=battery_id,
                            alert_type=ALERT_TYPE_DEVICE_OFFLINE,
                            severity=SEVERITY_WARNING,
                            message=(
                                f"{battery_id} has not reported telemetry in over "
                                f"{settings.OFFLINE_THRESHOLD_SECONDS:.0f} seconds"
                            ),
                            measured_value=None,
                            threshold_value=settings.OFFLINE_THRESHOLD_SECONDS,
                            timestamp=now,
                        )
                    await session.commit()
            if newly_offline_ids:
                logger.info("Marked %d battery(s) offline", len(newly_offline_ids))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - one bad tick must not kill the loop
            logger.exception("offline detection iteration failed")
        await asyncio.sleep(settings.OFFLINE_DETECTION_INTERVAL_SECONDS)
