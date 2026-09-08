"""Tests for the TelemetryEvent contract (app.schemas.telemetry)."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.telemetry import TelemetryEvent

VALID_EVENT = {
    "event_id": str(uuid4()),
    "battery_id": "BAT-000421",
    "timestamp": "2026-09-08T17:30:15.123Z",
    "state_of_charge": 42.3,
    "voltage": 51.2,
    "current": -12.4,
    "power_kw": -0.6,
    "temperature_c": 28.5,
    "health_percent": 97.5,
    "status": "DISCHARGING",
}


def test_valid_event_parses_successfully():
    event = TelemetryEvent(**VALID_EVENT)
    assert event.battery_id == "BAT-000421"
    assert event.status == "DISCHARGING"
    # Pydantic normalizes the "Z" suffix into a UTC-aware datetime.
    assert event.timestamp.tzinfo is not None


@pytest.mark.parametrize("bad_soc", [-0.1, 100.1])
def test_state_of_charge_out_of_bounds_rejected(bad_soc):
    with pytest.raises(ValidationError):
        TelemetryEvent(**{**VALID_EVENT, "state_of_charge": bad_soc})


@pytest.mark.parametrize("bad_health", [-1, 101])
def test_health_percent_out_of_bounds_rejected(bad_health):
    with pytest.raises(ValidationError):
        TelemetryEvent(**{**VALID_EVENT, "health_percent": bad_health})


def test_battery_id_must_match_format():
    with pytest.raises(ValidationError):
        TelemetryEvent(**{**VALID_EVENT, "battery_id": "not-a-real-id"})


def test_battery_id_wrong_digit_count_rejected():
    with pytest.raises(ValidationError):
        TelemetryEvent(**{**VALID_EVENT, "battery_id": "BAT-42"})


def test_event_id_must_be_uuid_v4():
    # A UUID v1 (time-based) string -- structurally a UUID, but the wrong version.
    uuid_v1 = "6ba7b810-9dad-11d1-80b4-00c04fd430c8"
    with pytest.raises(ValidationError):
        TelemetryEvent(**{**VALID_EVENT, "event_id": uuid_v1})


def test_timestamp_without_timezone_rejected():
    naive_timestamp = datetime(2026, 9, 8, 17, 30, 15).isoformat()
    with pytest.raises(ValidationError):
        TelemetryEvent(**{**VALID_EVENT, "timestamp": naive_timestamp})


def test_timestamp_with_timezone_accepted():
    aware_timestamp = datetime(2026, 9, 8, 17, 30, 15, tzinfo=timezone.utc).isoformat()
    event = TelemetryEvent(**{**VALID_EVENT, "timestamp": aware_timestamp})
    assert event.timestamp.tzinfo is not None


def test_status_must_be_a_supported_value():
    with pytest.raises(ValidationError):
        TelemetryEvent(**{**VALID_EVENT, "status": "OFFLINE"})  # backend-derived only


@pytest.mark.parametrize(
    "field,bad_value",
    [
        ("voltage", -1.0),
        ("voltage", 5000.0),
        ("temperature_c", -100.0),
        ("temperature_c", 500.0),
    ],
)
def test_plausible_bounds_reject_garbage_values(field, bad_value):
    with pytest.raises(ValidationError):
        TelemetryEvent(**{**VALID_EVENT, field: bad_value})


def test_missing_required_field_rejected():
    incomplete = {k: v for k, v in VALID_EVENT.items() if k != "battery_id"}
    with pytest.raises(ValidationError):
        TelemetryEvent(**incomplete)
