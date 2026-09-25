"""Periodic refresh for the two gauge-shaped metrics in
`app/core/metrics.py` (Roadmap 7.1): active batteries and active alerts.

Both are "current count" values rather than something that happens once per
request -- unlike the counters and histograms in
`app/services/telemetry_service.py`, which are updated inline on the
request path they measure, these are refreshed on their own schedule by
this background loop, the same shape as Roadmap 3.2's offline-detection
loop (`app/services/offline_detector.py`). Keeping them off the request
path matters after Phase 6: adding a database query to the hot ingestion
transaction is exactly the kind of extra lock-hold time 6.3/6.4 just
finished removing.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.metrics import ACTIVE_BATTERIES, ALERTS_ACTIVE
from app.db.session import async_session_maker
from app.models.alert import Alert
from app.models.battery_current_state import BatteryCurrentState

logger = logging.getLogger(__name__)

# Every alert severity this project's schema documents (app/models/alert.py:
# "INFO / WARNING / CRITICAL"), set explicitly to 0 when a severity has no
# active alerts right now rather than leaving that time series absent -- an
# absent series and a true zero look identical to Prometheus, but "this
# label combination doesn't exist yet" is worse for a dashboard or alerting
# rule that expects every severity to always report a value.
_ALERT_SEVERITIES = ("INFO", "WARNING", "CRITICAL")


async def _refresh_once() -> None:
    async with async_session_maker() as session:
        active_batteries = await session.scalar(
            select(func.count())
            .select_from(BatteryCurrentState)
            .where(BatteryCurrentState.status != "OFFLINE")
        )
        ACTIVE_BATTERIES.set(active_batteries or 0)

        rows = (
            await session.execute(
                select(Alert.severity, func.count())
                .where(Alert.resolved.is_(False))
                .group_by(Alert.severity)
            )
        ).all()
        counts_by_severity = dict(rows)
        for severity in _ALERT_SEVERITIES:
            ALERTS_ACTIVE.labels(severity=severity).set(counts_by_severity.get(severity, 0))


async def run_metrics_refresh_loop() -> None:
    """Background loop: keep the active-batteries and active-alerts gauges
    current for the lifetime of the process.

    Started/cancelled from `app/main.py`'s lifespan handler, the same
    pattern as `run_offline_detection_loop`. A single failed iteration
    (e.g. a momentary database hiccup) is logged and skipped rather than
    crashing the loop -- the next tick tries again.
    """
    settings = get_settings()
    while True:
        try:
            await _refresh_once()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - one bad tick must not kill the loop
            logger.exception("metrics refresh iteration failed")
        await asyncio.sleep(settings.METRICS_REFRESH_INTERVAL_SECONDS)
