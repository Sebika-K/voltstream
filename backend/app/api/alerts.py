"""Alert endpoints (Roadmap 3.4): `GET /api/v1/alerts` (list + filter) and
`PATCH /api/v1/alerts/{alert_id}` (manual resolve), per TDD section 11 and
Contract sections 26-27.

Roadmap 3.3 already made the `alerts` table correct (dedup, escalation,
auto-resolve); this router is purely a read/manual-write surface on top of
it -- no detection logic lives here.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.schemas.alert import AlertListResponse, AlertResponse, AlertSeverity, AlertType
from app.services.alert_service import list_alerts, resolve_alert

router = APIRouter(prefix="/api/v1/alerts", tags=["alerts"])


@router.get("", response_model=AlertListResponse)
async def list_alerts_endpoint(
    limit: int = Query(default=100, ge=1, le=500, description="Same cap as battery list"),
    offset: int = Query(default=0, ge=0),
    severity: AlertSeverity | None = Query(default=None),
    battery_id: str | None = Query(default=None),
    alert_type: AlertType | None = Query(default=None),
    resolved: bool | None = Query(default=None),
    session: AsyncSession = Depends(get_db_session),
) -> AlertListResponse:
    """List alerts, newest first, filterable by any combination of
    `severity`/`battery_id`/`alert_type`/`resolved` (TDD section 11).

    `resolved` is the filter an operator reaches for most: `?resolved=false`
    is "what's currently wrong with the fleet," `?resolved=true` is history.
    Omitting it returns both.
    """
    rows, total = await list_alerts(
        session,
        limit=limit,
        offset=offset,
        severity=severity,
        battery_id=battery_id,
        alert_type=alert_type,
        resolved=resolved,
    )
    return AlertListResponse(
        alerts=[AlertResponse.model_validate(alert) for alert in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.patch("/{alert_id}", response_model=AlertResponse)
async def resolve_alert_endpoint(
    alert_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
) -> AlertResponse:
    """Manually resolve one alert (Contract section 27: "manual resolution
    MAY be supported for operator workflow").

    Unknown `alert_id` -> `APIError` (404). This never disables detection --
    see `resolve_alert`'s docstring for exactly why a still-active condition
    can and will create a fresh alert again right after this.
    """
    alert = await resolve_alert(session, alert_id)
    return AlertResponse.model_validate(alert)
