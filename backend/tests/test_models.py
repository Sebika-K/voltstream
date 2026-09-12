"""Structural tests for the ORM models (Roadmap 1.5).

These tests never open a real database connection -- they only inspect the
`Table` objects SQLAlchemy builds from the model classes (columns, types,
nullability, primary/foreign keys, indexes). That's enough to catch "I typo'd
a column name" or "I forgot nullable=False" without needing PostgreSQL
running. Actually applying the migration against a real database is a
separate, manual verification step (see the project README).
"""

from __future__ import annotations

import uuid

from sqlalchemy import Date, DateTime, Double, String

from app.db.base import Base
from app.models.battery import Battery
from app.models.telemetry import Telemetry


def test_both_models_are_registered_on_the_shared_metadata():
    # This is what lets Alembic's autogenerate (and `alembic upgrade`) see these
    # tables at all -- if this fails, `app/models/__init__.py` or `alembic/env.py`
    # isn't importing something it should.
    assert "batteries" in Base.metadata.tables
    assert "telemetry" in Base.metadata.tables


def test_battery_tablename_and_primary_key():
    assert Battery.__tablename__ == "batteries"
    pk_columns = [c.name for c in Battery.__table__.primary_key.columns]
    assert pk_columns == ["battery_id"]


def test_battery_id_is_varchar_32():
    column = Battery.__table__.c.battery_id
    assert isinstance(column.type, String)
    assert column.type.length == 32


def test_battery_required_columns_are_not_nullable():
    required = [
        "battery_id",
        "capacity_kwh",
        "max_power_kw",
        "nominal_voltage",
        "profile_type",
        "created_at",
    ]
    for name in required:
        assert Battery.__table__.c[name].nullable is False, f"{name} should be NOT NULL"


def test_battery_optional_columns_are_nullable():
    for name in ("latitude", "longitude", "installation_date"):
        assert Battery.__table__.c[name].nullable is True, f"{name} should allow NULL"


def test_battery_numeric_columns_are_double_precision():
    numeric_columns = [
        "capacity_kwh",
        "max_power_kw",
        "nominal_voltage",
        "latitude",
        "longitude",
    ]
    for name in numeric_columns:
        assert isinstance(Battery.__table__.c[name].type, Double), name


def test_battery_installation_date_is_a_date_not_a_timestamp():
    assert isinstance(Battery.__table__.c.installation_date.type, Date)


def test_battery_created_at_is_timezone_aware_timestamp():
    column_type = Battery.__table__.c.created_at.type
    assert isinstance(column_type, DateTime)
    assert column_type.timezone is True


def test_telemetry_tablename_and_primary_key():
    assert Telemetry.__tablename__ == "telemetry"
    pk_columns = [c.name for c in Telemetry.__table__.primary_key.columns]
    assert pk_columns == ["event_id"]


def test_telemetry_all_columns_are_not_nullable():
    # Every telemetry column is required -- Contract section 20 lists none of
    # them as optional (unlike batteries' latitude/longitude/installation_date).
    for column in Telemetry.__table__.columns:
        assert column.nullable is False, f"{column.name} should be NOT NULL"


def test_telemetry_numeric_columns_are_double_precision():
    numeric_columns = [
        "state_of_charge",
        "voltage",
        "current",
        "power_kw",
        "temperature_c",
        "health_percent",
    ]
    for name in numeric_columns:
        assert isinstance(Telemetry.__table__.c[name].type, Double), name


def test_telemetry_battery_id_is_a_foreign_key_to_batteries():
    column = Telemetry.__table__.c.battery_id
    foreign_keys = list(column.foreign_keys)
    assert len(foreign_keys) == 1
    assert foreign_keys[0].target_fullname == "batteries.battery_id"


def test_telemetry_status_is_varchar_16():
    column = Telemetry.__table__.c.status
    assert isinstance(column.type, String)
    assert column.type.length == 16


def test_telemetry_has_the_required_battery_id_timestamp_index():
    index_names = {index.name for index in Telemetry.__table__.indexes}
    assert "ix_telemetry_battery_id_timestamp_desc" in index_names

    (index,) = [i for i in Telemetry.__table__.indexes if i.name == "ix_telemetry_battery_id_timestamp_desc"]
    indexed_column_names = [c.name for c in index.columns]
    # `timestamp DESC` is stored as raw SQL text (see telemetry.py's comment on
    # why), so only "battery_id" shows up as an actual named Column here --
    # that's expected, not a sign the DESC part got dropped.
    assert indexed_column_names == ["battery_id"]


def test_battery_repr_includes_identifying_fields():
    battery = Battery(
        battery_id="BAT-000001",
        capacity_kwh=10.0,
        max_power_kw=5.0,
        nominal_voltage=48.0,
        profile_type="RESIDENTIAL",
    )
    assert "BAT-000001" in repr(battery)
    assert "RESIDENTIAL" in repr(battery)


def test_telemetry_repr_includes_identifying_fields():
    event_id = uuid.uuid4()
    telemetry = Telemetry(
        event_id=event_id,
        battery_id="BAT-000001",
        state_of_charge=50.0,
        voltage=48.0,
        current=10.0,
        power_kw=1.0,
        temperature_c=25.0,
        health_percent=100.0,
        status="CHARGING",
    )
    assert str(event_id) in repr(telemetry)
    assert "BAT-000001" in repr(telemetry)
