"""SQLAlchemy ORM model for the `batteries` table.

This is device *metadata* -- capacity, wiring limits, location, which usage
profile it runs -- set once at registration and rarely changed. It is
deliberately separate from `telemetry.py`'s table, which holds every reading a
battery has ever reported: a battery needs to exist here before any telemetry
for it can be accepted (Contract section 7: "Telemetry for an unregistered
battery MUST be rejected").

Column set and types follow the Implementation Contract section 20 database
schema contract exactly -- nothing here is a free design choice.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import Date, DateTime, Double, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Battery(Base):
    """A registered battery device."""

    __tablename__ = "batteries"

    # Contract section 4.1: format BAT-######, max length VARCHAR(32).
    battery_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    capacity_kwh: Mapped[float] = mapped_column(Double, nullable=False)
    max_power_kw: Mapped[float] = mapped_column(Double, nullable=False)
    nominal_voltage: Mapped[float] = mapped_column(Double, nullable=False)
    # Location is optional -- the Contract lists it as a registration field but
    # doesn't say every simulated device must have real-world coordinates.
    latitude: Mapped[float | None] = mapped_column(Double, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Double, nullable=True)
    installation_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    # RESIDENTIAL / SOLAR / COMMERCIAL / FAULTY (validated by whatever writes
    # this row -- e.g. a Pydantic schema at the API layer -- not by the database
    # itself; the column is a plain VARCHAR(32), not a Postgres ENUM, matching
    # the Contract's literal column type).
    profile_type: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    def __repr__(self) -> str:  # pragma: no cover - debugging convenience only
        return f"Battery(battery_id={self.battery_id!r}, profile_type={self.profile_type!r})"
