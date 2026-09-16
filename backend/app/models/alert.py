"""SQLAlchemy ORM model for the `alerts` table (Roadmap 3.3).

Column set, types, and the foreign key all follow the Implementation
Contract section 20 database schema contract exactly. This is the first
table written by more than one kind of code: telemetry-triggered rules
(`app/services/anomaly_detection_service.py`, run during ingestion, for
`LOW_SOC`/`HIGH_TEMPERATURE`/`RAPID_DISCHARGE`/`VOLTAGE_ANOMALY`) and the
Roadmap 3.2 offline-detection loop (`app/services/offline_detector.py`, for
`DEVICE_OFFLINE`) -- both go through the same shared create/retain/resolve
helpers in `anomaly_detection_service.py` rather than writing rows directly,
so the "one unresolved alert per battery_id + alert_type" rule (Contract
section 26) only has to be implemented once.

`measured_value`/`threshold_value` are nullable because not every alert type
has a single natural number to put there the same way `LOW_SOC`'s SOC or
`HIGH_TEMPERATURE`'s temperature does -- `DEVICE_OFFLINE` doesn't reduce to
one measured value, so both are left null for that alert type.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import Boolean, DateTime, Double, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Alert(Base):
    """One alert incident for one battery (Contract sections 24-26)."""

    __tablename__ = "alerts"

    # Contract section 4.3: alert_id MUST be UUID v4.
    alert_id: Mapped[uuid.UUID] = mapped_column(PostgresUUID(as_uuid=True), primary_key=True)
    battery_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("batteries.battery_id", ondelete="CASCADE"), nullable=False
    )
    timestamp: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # LOW_SOC / HIGH_TEMPERATURE / RAPID_DISCHARGE / VOLTAGE_ANOMALY / DEVICE_OFFLINE
    # (Contract section 24) -- a plain VARCHAR, not a Postgres ENUM, same
    # choice already made for `battery_current_state.status` and
    # `telemetry.status` elsewhere in this schema.
    alert_type: Mapped[str] = mapped_column(String(64), nullable=False)
    # INFO / WARNING / CRITICAL (PRD FR-9).
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    measured_value: Mapped[float | None] = mapped_column(Double, nullable=True)
    threshold_value: Mapped[float | None] = mapped_column(Double, nullable=True)
    resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    resolved_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        # Contract section 26: "only one unresolved alert of a given
        # alert_type MAY exist for a battery at a time." A partial unique
        # index (only over rows where resolved = false) is the
        # database-level enforcement of that rule -- not just a lookup
        # index -- so the invariant holds even if the application's own
        # check-then-insert logic (app/services/anomaly_detection_service.py)
        # ever has a bug.
        Index(
            "ux_alerts_battery_id_alert_type_unresolved",
            "battery_id",
            "alert_type",
            unique=True,
            postgresql_where=text("resolved = false"),
        ),
        # Backs GET /api/v1/fleet/summary's active_alerts/critical_alerts
        # counts (Roadmap 2.4), once that endpoint reads from this table.
        Index("ix_alerts_resolved", "resolved"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging convenience only
        return (
            f"Alert(alert_id={self.alert_id!r}, battery_id={self.battery_id!r}, "
            f"alert_type={self.alert_type!r}, severity={self.severity!r}, "
            f"resolved={self.resolved!r})"
        )
