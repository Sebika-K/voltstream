"""SQLAlchemy ORM model for the `battery_current_state` table (Roadmap 2.1).

`telemetry.py`'s table is append-only history -- answering "what is this
battery doing right now" from it means scanning for the newest row every
single time, which gets slower as history grows. This table exists purely to
avoid that: one row per battery, always overwritten (never appended to) with
whatever the latest known reading is. Historical telemetry stays the
authoritative record for history; this table is only ever a fast, disposable
mirror of "the newest of it" (Contract section 21).

A registered battery that has never sent telemetry has no row here at all --
this table is populated by telemetry ingestion, not by registration (Contract
section 21: "API code MUST handle this case explicitly rather than
fabricating telemetry").

Column set and types follow the Implementation Contract section 20 database
schema contract. The Contract doesn't list a foreign key for this table the
way it explicitly does for `telemetry`, but adding one anyway is a reasonable
call here: every write to this table happens *because* a telemetry event
already passed the "battery is registered" check (see
app/services/telemetry_service.py), so the constraint costs nothing and
catches bugs early rather than silently orphaning a row.

`ondelete="CASCADE"` on that foreign key matters: this row has no meaning of
its own once its battery is gone -- it is a disposable mirror of the newest
telemetry, not a historical record (that's `telemetry`'s job). Without
CASCADE, deleting a battery would fail with a foreign-key error the moment it
had ever reported a single reading, which is both surprising and unrelated to
what the caller is actually trying to do.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import DateTime, Double, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class BatteryCurrentState(Base):
    """The latest known telemetry reading for one battery."""

    __tablename__ = "battery_current_state"

    battery_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("batteries.battery_id", ondelete="CASCADE"), primary_key=True
    )
    last_seen: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    state_of_charge: Mapped[float] = mapped_column(Double, nullable=False)
    temperature_c: Mapped[float] = mapped_column(Double, nullable=False)
    power_kw: Mapped[float] = mapped_column(Double, nullable=False)
    health_percent: Mapped[float] = mapped_column(Double, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)

    def __repr__(self) -> str:  # pragma: no cover - debugging convenience only
        return (
            f"BatteryCurrentState(battery_id={self.battery_id!r}, "
            f"status={self.status!r}, last_seen={self.last_seen!r})"
        )
