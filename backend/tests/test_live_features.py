"""Tests for live ML feature computation (Roadmap 4.5, Contract section
41).

Pure, synchronous, hand-computed -- the same style
`tests/test_prediction_baseline.py` and `ml/tests/test_features.py` already
use. `ml/tests/test_features.py` proves the training-side math; this file
proves the serving-side reimplementation of that same math (see
`app/services/live_features.py`'s module docstring for why it's a separate
implementation, not a shared import, and how the two are kept honest
against each other).
"""

from __future__ import annotations

import datetime as dt

from app.services.live_features import compute_live_features

AS_OF = dt.datetime(2026, 1, 1, 14, 30, 0, tzinfo=dt.timezone.utc)


def _reading(minutes_ago: float, soc: float, power_kw: float) -> tuple[dt.datetime, float, float]:
    return (AS_OF - dt.timedelta(minutes=minutes_ago), soc, power_kw)


def test_rolling_power_5m_is_the_exact_mean_including_the_current_reading():
    # A reading 6 minutes back exists purely so `soc_change_5m` has
    # something to compute against (this function refuses to guess a
    # feature vector at all when that one part is missing -- see
    # `test_soc_change_5m_is_none_without_a_reading_that_old` below); it's
    # outside the 5-minute rolling-power window, so it must NOT affect
    # this test's own math.
    history = [
        _reading(6, soc=52.0, power_kw=-1.5),
        _reading(4, soc=50.0, power_kw=-2.0),
        _reading(2, soc=48.0, power_kw=-3.0),
    ]
    features = compute_live_features(
        history=history,
        as_of=AS_OF,
        current_soc=46.0,
        current_power_kw=-4.0,
        temperature_c=25.0,
        health_percent=98.0,
        capacity_kwh=10.0,
        profile_type="RESIDENTIAL",
    )
    assert features is not None
    # (-2, -3, -4) averaged over the trailing 5 minutes, current reading
    # included -- the 6-minutes-back reading is correctly excluded.
    assert features.rolling_power_5m == -3.0


def test_rolling_power_15m_includes_a_reading_the_5m_window_excludes():
    history = [_reading(12, soc=55.0, power_kw=-1.0), _reading(2, soc=48.0, power_kw=-3.0)]
    features = compute_live_features(
        history=history,
        as_of=AS_OF,
        current_soc=46.0,
        current_power_kw=-5.0,
        temperature_c=25.0,
        health_percent=98.0,
        capacity_kwh=10.0,
        profile_type="RESIDENTIAL",
    )
    assert features is not None
    assert features.rolling_power_5m == -4.0  # only the 2-min-ago reading + current
    assert features.rolling_power_15m == -3.0  # (-1, -3, -5) / 3


def test_soc_change_5m_is_negative_while_discharging():
    history = [_reading(5, soc=50.0, power_kw=-2.0)]
    features = compute_live_features(
        history=history,
        as_of=AS_OF,
        current_soc=44.0,
        current_power_kw=-2.0,
        temperature_c=25.0,
        health_percent=98.0,
        capacity_kwh=10.0,
        profile_type="RESIDENTIAL",
    )
    assert features is not None
    assert features.soc_change_5m == -6.0


def test_soc_change_5m_is_none_without_a_reading_that_old():
    history = [_reading(2, soc=48.0, power_kw=-2.0)]  # only 2 minutes of history
    features = compute_live_features(
        history=history,
        as_of=AS_OF,
        current_soc=46.0,
        current_power_kw=-2.0,
        temperature_c=25.0,
        health_percent=98.0,
        capacity_kwh=10.0,
        profile_type="RESIDENTIAL",
    )
    assert features is None


def test_no_history_at_all_still_returns_none_not_a_crash():
    features = compute_live_features(
        history=[],
        as_of=AS_OF,
        current_soc=46.0,
        current_power_kw=-2.0,
        temperature_c=25.0,
        health_percent=98.0,
        capacity_kwh=10.0,
        profile_type="RESIDENTIAL",
    )
    assert features is None


def test_static_fields_and_hour_of_day_pass_through_unchanged():
    history = [_reading(5, soc=50.0, power_kw=-2.0)]
    features = compute_live_features(
        history=history,
        as_of=AS_OF,
        current_soc=44.0,
        current_power_kw=-2.5,
        temperature_c=31.5,
        health_percent=96.0,
        capacity_kwh=13.5,
        profile_type="SOLAR",
    )
    assert features is not None
    assert features.state_of_charge == 44.0
    assert features.power_kw == -2.5
    assert features.temperature_c == 31.5
    assert features.health_percent == 96.0
    assert features.capacity_kwh == 13.5
    assert features.profile_type == "SOLAR"
    assert features.hour_of_day == 14  # AS_OF is 14:30 UTC
