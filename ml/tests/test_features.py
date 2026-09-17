"""Tests for the shared V1 feature computation (Roadmap 4.2, Contract
section 41).

Every test builds a small, hand-picked `history` DataFrame with exact
timestamps and values, so the rolling-window math can be checked against
numbers computed by hand -- the same "prove the formula, don't approximate
it" approach used for the physics baseline in
`backend/tests/test_prediction_baseline.py`.
"""

from __future__ import annotations

import math

import pandas as pd

from features import compute_features_for_point


def _history(rows: list[dict]) -> pd.DataFrame:
    """Build a one-battery history DataFrame from a list of
    {minutes_ago, state_of_charge, power_kw, temperature_c, health_percent}
    dicts, timestamped backward from a fixed anchor, sorted ascending --
    exactly the shape `compute_features_for_point` expects."""
    anchor = pd.Timestamp("2026-01-01T12:00:00Z")
    df = pd.DataFrame(
        [
            {
                "timestamp": anchor - pd.Timedelta(minutes=row["minutes_ago"]),
                "state_of_charge": row["state_of_charge"],
                "power_kw": row["power_kw"],
                "temperature_c": row.get("temperature_c", 25.0),
                "health_percent": row.get("health_percent", 98.0),
            }
            for row in rows
        ]
    )
    return df.sort_values("timestamp").reset_index(drop=True)


def test_rolling_power_is_the_exact_mean_over_the_window():
    # Three readings inside the trailing 5 minutes: -2.0, -3.0, -4.0 kW.
    # Mean = -3.0 exactly.
    history = _history(
        [
            {"minutes_ago": 4, "state_of_charge": 60.0, "power_kw": -2.0},
            {"minutes_ago": 2, "state_of_charge": 55.0, "power_kw": -3.0},
            {"minutes_ago": 0, "state_of_charge": 50.0, "power_kw": -4.0},
        ]
    )
    features = compute_features_for_point(history, capacity_kwh=10.0, profile_type="RESIDENTIAL")

    assert features.rolling_power_5m == -3.0
    assert features.power_kw == -4.0
    assert features.state_of_charge == 50.0


def test_rolling_power_15m_includes_a_reading_the_5m_window_excludes():
    history = _history(
        [
            {"minutes_ago": 12, "state_of_charge": 70.0, "power_kw": -1.0},
            {"minutes_ago": 3, "state_of_charge": 60.0, "power_kw": -3.0},
            {"minutes_ago": 0, "state_of_charge": 55.0, "power_kw": -5.0},
        ]
    )
    features = compute_features_for_point(history, capacity_kwh=10.0, profile_type="SOLAR")

    # 5m window only sees the last two readings: mean(-3.0, -5.0) = -4.0
    assert features.rolling_power_5m == -4.0
    # 15m window sees all three: mean(-1.0, -3.0, -5.0) = -3.0
    assert features.rolling_power_15m == -3.0


def test_soc_change_5m_is_negative_while_discharging():
    # SOC was 65.0 five minutes ago (at the cutoff), now 50.0 -> change = -15.0
    history = _history(
        [
            {"minutes_ago": 5, "state_of_charge": 65.0, "power_kw": -3.0},
            {"minutes_ago": 2, "state_of_charge": 58.0, "power_kw": -3.5},
            {"minutes_ago": 0, "state_of_charge": 50.0, "power_kw": -4.0},
        ]
    )
    features = compute_features_for_point(history, capacity_kwh=10.0, profile_type="COMMERCIAL")

    assert features.soc_change_5m == -15.0


def test_soc_change_5m_is_positive_while_charging():
    history = _history(
        [
            {"minutes_ago": 5, "state_of_charge": 40.0, "power_kw": 3.0},
            {"minutes_ago": 0, "state_of_charge": 47.0, "power_kw": 3.2},
        ]
    )
    features = compute_features_for_point(history, capacity_kwh=10.0, profile_type="RESIDENTIAL")

    assert features.soc_change_5m == 7.0


def test_soc_change_5m_is_nan_without_a_reading_that_old():
    # Only 2 minutes of history exist -- nothing at or before the 5-minute
    # cutoff to compare against.
    history = _history(
        [
            {"minutes_ago": 2, "state_of_charge": 50.0, "power_kw": -2.0},
            {"minutes_ago": 0, "state_of_charge": 49.0, "power_kw": -2.0},
        ]
    )
    features = compute_features_for_point(history, capacity_kwh=10.0, profile_type="RESIDENTIAL")

    assert math.isnan(features.soc_change_5m)


def test_hour_of_day_reflects_the_current_points_timestamp():
    history = _history([{"minutes_ago": 0, "state_of_charge": 50.0, "power_kw": -2.0}])
    features = compute_features_for_point(history, capacity_kwh=10.0, profile_type="RESIDENTIAL")

    # The anchor timestamp used by `_history` is 12:00:00Z.
    assert features.hour_of_day == 12


def test_static_metadata_passes_through_unchanged():
    history = _history(
        [{"minutes_ago": 0, "state_of_charge": 33.0, "power_kw": -1.5, "temperature_c": 41.2, "health_percent": 91.0}]
    )
    features = compute_features_for_point(history, capacity_kwh=13.5, profile_type="FAULTY")

    assert features.capacity_kwh == 13.5
    assert features.profile_type == "FAULTY"
    assert features.temperature_c == 41.2
    assert features.health_percent == 91.0
