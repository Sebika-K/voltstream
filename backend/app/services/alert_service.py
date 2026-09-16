"""Alert read/resolve logic (Roadmap 3.4, Contract sections 26-27).

Kept separate from `app/api/alerts.py` for the same reason
`app/services/battery_service.py` is kept separate from its router: the
router's job is "translate HTTP into a call here, translate the result back
into HTTP"; this module's job is the actual query/update logic, with no
FastAPI or HTTP status codes anywhere in it.

Roadmap 3.3 already built the hard part -- `create_or_retain_alert` /
`resolve_alert_if_active` in `app/services/anomaly_detection_service.py`,
which is what keeps "one unresolved alert per battery_id + alert_type" true
at all times. This module doesn't touch that logic at all; it only reads
from the `alerts` table, and adds the one write path Roadmap 3.4 asks for:
letting a person manually resolve an alert (as opposed to the automatic
resolution the rules themselves already do).
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import APIError
from app.models.alert import Alert


async def list_alerts(
    session: AsyncSession,
    *,
    limit: int,
    offset: int,
    severity: str | None = None,
    battery_id: str | None = None,
    alert_type: str | None = None,
    resolved: bool | None = None,
) -> tuple[list[Alert], int]:
    """List alerts (TDD section 11: `severity`/`battery_id`/`alert_type`/
    `resolved` are all optional filters, combinable), paginated the same
    `limit`/`offset` way `list_batteries` (Roadmap 2.2) already is.

    Newest-first ordering (`timestamp DESC`) is a reasonable default of our
    own -- the Contract doesn't specify one for this endpoint, but every
    other list-shaped response in this project (telemetry history) already
    defaults to newest-first, and "what just happened" is what an operator
    opening this endpoint almost always wants to see first.

    Returns `(rows, total)`, same pattern as `list_batteries`: `rows` is this
    page's alerts; `total` is how many match the filters in total, ignoring
    pagination, for rendering "page N of M."
    """
    base_query = select(Alert)

    filters = []
    if severity is not None:
        filters.append(Alert.severity == severity)
    if battery_id is not None:
        filters.append(Alert.battery_id == battery_id)
    if alert_type is not None:
        filters.append(Alert.alert_type == alert_type)
    if resolved is not None:
        filters.append(Alert.resolved.is_(resolved))
    if filters:
        base_query = base_query.where(*filters)

    total = await session.scalar(select(func.count()).select_from(base_query.subquery()))

    paginated_query = base_query.order_by(Alert.timestamp.desc()).limit(limit).offset(offset)
    result = await session.execute(paginated_query)
    rows = list(result.scalars().all())
    return rows, total or 0


async def resolve_alert(session: AsyncSession, alert_id: uuid.UUID) -> Alert:
    """Manually resolve one alert (Contract section 27).

    Raises `APIError` (404) if `alert_id` doesn't exist at all. Resolving an
    alert that's *already* resolved is a safe, idempotent no-op -- it's
    returned as-is rather than treated as an error, the same "already true,
    nothing to do" spirit as `register_battery`'s identical-metadata
    re-registration case.

    Contract section 27 is explicit that this MUST NOT disable anomaly
    detection: nothing here touches `app/services/anomaly_detection_service.py`
    or the rules that call it, so if the underlying condition is still
    active, the very next telemetry event for this battery (or the very next
    offline-detection tick, for `DEVICE_OFFLINE`) creates a brand-new alert
    with a new `alert_id` -- exactly like any other recurrence after
    resolution. This function only ever writes `resolved`/`resolved_at`; it
    never suppresses future detection.
    """
    alert = await session.get(Alert, alert_id)
    if alert is None:
        raise APIError(
            status_code=404,
            code="ALERT_NOT_FOUND",
            message=f"Alert {alert_id} was not found",
        )

    if not alert.resolved:
        alert.resolved = True
        alert.resolved_at = datetime.datetime.now(datetime.timezone.utc)
        await session.commit()
        await session.refresh(alert)

    return alert
