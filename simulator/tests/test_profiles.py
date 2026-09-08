"""Unit tests for battery usage profiles."""

from __future__ import annotations

import random

from battery import ProfileType
from profiles import base_power_fraction, requested_power_kw


def test_residential_table_spot_checks():
    assert base_power_fraction(ProfileType.RESIDENTIAL, 0) == -0.15
    assert base_power_fraction(ProfileType.RESIDENTIAL, 8) == -0.35
    assert base_power_fraction(ProfileType.RESIDENTIAL, 9) == -0.15
    assert base_power_fraction(ProfileType.RESIDENTIAL, 18) == -0.55
    assert base_power_fraction(ProfileType.RESIDENTIAL, 22) == -0.55
    assert base_power_fraction(ProfileType.RESIDENTIAL, 23) == -0.25


def test_solar_charges_during_daytime_and_discharges_overnight():
    assert base_power_fraction(ProfileType.SOLAR, 12) == 0.45  # midday: charging
    assert base_power_fraction(ProfileType.SOLAR, 2) == -0.10  # middle of the night


def test_commercial_table_spot_checks():
    assert base_power_fraction(ProfileType.COMMERCIAL, 10) == -0.50  # business hours
    assert base_power_fraction(ProfileType.COMMERCIAL, 3) == -0.05


def test_faulty_uses_residential_as_its_base():
    for hour in range(24):
        assert base_power_fraction(ProfileType.FAULTY, hour) == base_power_fraction(
            ProfileType.RESIDENTIAL, hour
        )


def test_every_hour_of_day_is_covered_for_every_real_profile():
    for profile in (ProfileType.RESIDENTIAL, ProfileType.SOLAR, ProfileType.COMMERCIAL):
        for hour in range(24):
            # Should not raise -- the table must be exhaustive across 0-23.
            base_power_fraction(profile, hour)


def test_hour_out_of_range_raises():
    for bad_hour in (24, -1):
        try:
            base_power_fraction(ProfileType.RESIDENTIAL, bad_hour)
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected ValueError for hour={bad_hour}")


def test_requested_power_stays_within_variation_bounds():
    rng = random.Random(0)
    max_power_kw = 5.0
    # Residential at hour 0 -> base fraction -0.15, +/-5% variation.
    for _ in range(50):
        power = requested_power_kw(ProfileType.RESIDENTIAL, max_power_kw, 0, rng)
        assert -0.20 * max_power_kw <= power <= -0.10 * max_power_kw


def test_requested_power_reproducible_with_same_rng_state():
    power_a = requested_power_kw(ProfileType.SOLAR, 5.0, 12, random.Random(7))
    power_b = requested_power_kw(ProfileType.SOLAR, 5.0, 12, random.Random(7))
    assert power_a == power_b
