"""Tests for the eligibility + target-generation logic in
`ml/training/build_dataset.py` (Roadmap 4.2, Contract section 42).

These build a single battery's telemetry history by hand -- exact
timestamps and SOC values -- so each of the four eligibility rules, and
the exact `minutes_to_critical` target math, can be checked precisely
rather than just "did something get written." No database is involved:
`_build_rows_for_battery` takes a plain DataFrame, which is exactly what
makes it testable without Postgres.
"""

from __future__ import annotations

from collections import Counter

import pandas as pd

from training.build_dataset import _build_rows_for_battery, _next_critical_index


def _telemetry(rows: list[dict]) -> pd.DataFrame:
    """rows: list of {minutes, soc, power_kw, status} where `minutes` is
    minutes since a fixed start -- ascending, matching how the real query
    orders telemetry."""
    start = pd.Timestamp("2026-01-01T00:00:00Z")
    return pd.DataFrame(
        [
            {
                "timestamp": start + pd.Timedelta(minutes=row["minutes"]),
                "state_of_charge": row["soc"],
                "power_kw": row["power_kw"],
                "temperature_c": row.get("temperature_c", 25.0),
                "health_percent": row.get("health_percent", 98.0),
                "status": row["status"],
            }
            for row in rows
        ]
    )


def test_next_critical_index_finds_the_nearest_future_critical_point():
    # SOC touches <=20 at positions 2 and 4.
    soc = pd.Series([50.0, 30.0, 20.0, 25.0, 15.0]).to_numpy()
    result = _next_critical_index(soc)

    assert list(result) == [2, 2, 4, 4, -1]


def test_eligible_row_gets_the_exact_minutes_to_critical_target():
    # 16+ minutes of DISCHARGING history (full 15-min lookback available at
    # the last point), SOC only reaches critical at the very last reading.
    rows = [{"minutes": m, "soc": 80.0 - m, "power_kw": -2.0, "status": "DISCHARGING"} for m in range(0, 17)]
    # last point (minute 16): soc = 80 - 16 = 64, still above 20 -- adjust so
    # a later point actually goes critical.
    rows.append({"minutes": 20, "soc": 20.0, "power_kw": -2.0, "status": "DISCHARGING"})
    telemetry = _telemetry(rows)

    stats = Counter()
    result = _build_rows_for_battery(
        "BAT-000001", telemetry, capacity_kwh=10.0, profile_type="RESIDENTIAL", stats=stats
    )

    # The point at minute 16 (soc=64) has a full 15-minute lookback (first
    # reading at minute 0) and a future critical point at minute 20 -- 4
    # minutes later.
    row_at_16 = next(r for r in result if r["timestamp"] == pd.Timestamp("2026-01-01T00:16:00Z"))
    assert row_at_16["minutes_to_critical"] == 4.0
    assert row_at_16["state_of_charge"] == 64.0
    assert stats["eligible"] >= 1


def test_row_already_at_or_below_critical_is_excluded():
    rows = [
        {"minutes": m, "soc": 25.0, "power_kw": -2.0, "status": "DISCHARGING"} for m in range(0, 16)
    ]
    rows.append({"minutes": 16, "soc": 18.0, "power_kw": -2.0, "status": "DISCHARGING"})
    telemetry = _telemetry(rows)

    stats = Counter()
    result = _build_rows_for_battery(
        "BAT-000002", telemetry, capacity_kwh=10.0, profile_type="RESIDENTIAL", stats=stats
    )

    # The minute-16 point itself (soc=18) is excluded for being at/below
    # critical already -- it should never appear as a row.
    assert all(r["timestamp"] != pd.Timestamp("2026-01-01T00:16:00Z") for r in result)
    assert stats["already_at_or_below_critical"] >= 1


def test_charging_battery_produces_no_rows():
    rows = [{"minutes": m, "soc": 50.0 + m, "power_kw": 2.0, "status": "CHARGING"} for m in range(0, 20)]
    telemetry = _telemetry(rows)

    stats = Counter()
    result = _build_rows_for_battery(
        "BAT-000003", telemetry, capacity_kwh=10.0, profile_type="SOLAR", stats=stats
    )

    assert result == []
    assert stats["not_discharging"] == 20


def test_insufficient_history_is_excluded_even_if_otherwise_eligible():
    # Only 10 minutes of history -- never reaches the 15-minute lookback
    # requirement, even though it's discharging and later goes critical.
    rows = [{"minutes": m, "soc": 80.0 - m, "power_kw": -2.0, "status": "DISCHARGING"} for m in range(0, 11)]
    telemetry = _telemetry(rows)

    stats = Counter()
    result = _build_rows_for_battery(
        "BAT-000004", telemetry, capacity_kwh=10.0, profile_type="RESIDENTIAL", stats=stats
    )

    assert result == []
    assert stats["insufficient_history"] == 11


def test_no_future_critical_point_is_excluded_not_fabricated():
    # 30 minutes of discharging history, but SOC never actually drops to
    # <=20 anywhere in the stored data (recovers instead) -- Contract
    # section 42: these rows get no target at all, not an approximate one.
    rows = [{"minutes": m, "soc": 60.0, "power_kw": -1.0, "status": "DISCHARGING"} for m in range(0, 30)]
    telemetry = _telemetry(rows)

    stats = Counter()
    result = _build_rows_for_battery(
        "BAT-000005", telemetry, capacity_kwh=10.0, profile_type="COMMERCIAL", stats=stats
    )

    assert result == []
    assert stats["no_future_critical_point"] > 0
