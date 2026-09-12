"""SQLAlchemy ORM model for the `telemetry` table.

This table is append-only history (Contract invariants #4 "historical telemetry
is append-oriented" and #5 "out-of-order telemetry MAY be stored historically").
Rows are never updated or deleted during normal ingestion -- a later table
(`battery_current_state`, Roadmap 2.1, not built yet) is what will answer "what
is this battery doing *right now*" without re-scanning this table's full
history every time.

Column set, types, the foreign key, and the required index all follow the
Implementation Contract section 20 database schema contract exactly.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import DateTime, Double, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Telemetry(Base):
    """One reported telemetry reading for a battery."""

    __tablename__ = "telemetry"

    # Contract section 4.2: event_id MUST be UUID v4, and is the idempotency
    # identity for telemetry -- hence PRIMARY KEY rather than just a unique
    # index (this is also what Roadmap 1.8's "make event_id unique" means).
    event_id: Mapped[uuid.UUID] = mapped_column(PostgresUUID(as_uuid=True), primary_key=True)
    battery_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("batteries.battery_id"), nullable=False
    )
    timestamp: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    state_of_charge: Mapped[float] = mapped_column(Double, nullable=False)
    voltage: Mapped[float] = mapped_column(Double, nullable=False)
    current: Mapped[float] = mapped_column(Double, nullable=False)
    power_kw: Mapped[float] = mapped_column(Double, nullable=False)
    temperature_c: Mapped[float] = mapped_column(Double, nullable=False)
    health_percent: Mapped[float] = mapped_column(Double, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)

    __table_args__ = (
        # Contract section 20's required index. "battery_id" is resolved by name
        # against this table's own columns; text("timestamp DESC") is how you
        # tell Postgres the second column should be indexed descending -- SQLAlchemy
        # has no separate "descending column" object, so raw SQL text is the
        # standard way to express it here.
        Index(
            "ix_telemetry_battery_id_timestamp_desc",
            "battery_id",
            text("timestamp DESC"),
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging convenience only
        return f"Telemetry(event_id={self.event_id!r}, battery_id={self.battery_id!r})"
