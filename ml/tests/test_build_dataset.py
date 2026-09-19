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


def test_restart_gap_that_slips_past_the_15min_check_is_still_excluded():
    # Reproduces the real bug found in the Roadmap 4.4 re-run: an "old run"
    # segment (minutes 0-20), then a ~4.5-hour gap (the simulator was
    # stopped and restarted), then a "new run" segment starting at minute
    # 300. Check 3 only compares `as_of` against this battery's very
    # first-ever reading (minute 0), so it wrongly considers 300+ minutes
    # of "history" to exist, even though the real continuous window right
    # before minute 303 is only 3 minutes long. `soc_change_5m` needs a
    # reading from 5 minutes back and can't find one -- this used to slip
    # through as a NaN feature value instead of being excluded.
    old_run = [{"minutes": m, "soc": 80.0 - m, "power_kw": -2.0, "status": "DISCHARGING"} for m in range(0, 21)]
    new_run = [
        {"minutes": 300 + m, "soc": 80.0 - 3.0 * m, "power_kw": -2.0, "status": "DISCHARGING"} for m in range(0, 21)
    ]
    telemetry = _telemetry(old_run + new_run)

    stats = Counter()
    result = _build_rows_for_battery(
        "BAT-000006", telemetry, capacity_kwh=10.0, profile_type="RESIDENTIAL", stats=stats
    )

    # The point 3 minutes after the restart (minute 303) must NOT appear --
    # not as a row with a NaN feature, not at all.
    assert all(row["timestamp"] != pd.Timestamp("2026-01-01T00:00:00Z") + pd.Timedelta(minutes=303) for row in result)
    # 15 old-run rows (minutes 0-14) are genuinely too early in that run's
    # own history, plus 5 new-run rows (minutes 300-304, the first 5
    # minutes after the restart) that check 3 wrongly let through but the
    # NaN check now catches.
    assert stats["insufficient_history"] == 20

    # No row in the output ever has a NaN feature value -- the whole point
    # of the fix.
    for row in result:
        for key, value in row.items():
            assert not (isinstance(value, float) and pd.isna(value)), f"{key} is NaN in row {row}"

    # A point far enough past the restart to have a real 5-minute window
    # behind it (minute 308: 8 real minutes of new-run data) is still
    # correctly included, with a real (non-NaN) soc_change_5m.
    eligible_308 = [
        row
        for row in result
        if row["timestamp"] == pd.Timestamp("2026-01-01T00:00:00Z") + pd.Timedelta(minutes=308)
    ]
    assert len(eligible_308) == 1
    assert eligible_308[0]["soc_change_5m"] == -15.0  # loses 3.0/min for 5 minutes
