"""Periodic offline-detection loop (Roadmap 3.2, Contract section 22:
"offline detection runs periodically outside the primary telemetry request
path").

Started from `app/main.py`'s lifespan alongside the Roadmap 3.1
`fleet_update` publisher -- a second, independent background task rather
than folded into the first one, since the two run on different natural
schedules (1/sec vs. every few seconds) and a failure in one has nothing to
do with the other.
"""

from __future__ import annotations

import asyncio
import logging

from app.core.config import get_settings
from app.db.session import async_session_maker
from app.services.offline_detection_service import mark_stale_batteries_offline

logger = logging.getLogger(__name__)


async def run_offline_detection_loop() -> None:
    """Background loop: check for, and mark, newly-stale batteries.

    Runs for the lifetime of the process, started/cancelled from
    `app/main.py`'s lifespan handler. A single failed iteration (e.g. a
    momentary database hiccup) is logged and skipped rather than crashing
    the loop -- the next tick, a few seconds later, tries again.
    """
    settings = get_settings()
    while True:
        try:
            async with async_session_maker() as session:
                newly_offline = await mark_stale_batteries_offline(session)
            if newly_offline:
                logger.info("Marked %d battery(s) offline", newly_offline)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - one bad tick must not kill the loop
            logger.exception("offline detection iteration failed")
        await asyncio.sleep(settings.OFFLINE_DETECTION_INTERVAL_SECONDS)
