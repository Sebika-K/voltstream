"""profiles.py -- usage-profile power patterns (Implementation Contract section 10).

Each profile decides what fraction of a battery's max_power_kw it *wants* to draw at
a given hour of simulated local time. This module never touches a Battery -- it just
produces a requested power number; `Battery.tick()` (step 1) still owns turning a
request into reality (SOC limits, max-power clamping, etc.).
"""

from __future__ import annotations

import random

from battery import ProfileType

# Implementation Contract section 10: fraction of max_power_kw, by hour-of-day range.
# Positive fractions charge, negative discharge -- same sign convention Battery uses.
_HOURLY_POWER_FRACTION: dict[ProfileType, tuple[tuple[range, float], ...]] = {
    ProfileType.RESIDENTIAL: (
        (range(0, 6), -0.15),
        (range(6, 9), -0.35),
        (range(9, 16), -0.15),
        (range(16, 19), -0.55),
        (range(19, 23), -0.55),
        (range(23, 24), -0.25),
    ),
    ProfileType.SOLAR: (
        (range(0, 6), -0.10),
        (range(6, 9), -0.05),
        (range(9, 16), 0.45),
        (range(16, 19), 0.10),
        (range(19, 23), -0.25),
        (range(23, 24), -0.15),
    ),
    ProfileType.COMMERCIAL: (
        (range(0, 6), -0.05),
        (range(6, 9), -0.20),
        (range(9, 16), -0.50),
        (range(16, 19), -0.50),
        (range(19, 23), -0.20),
        (range(23, 24), -0.05),
    ),
}

# Implementation Contract section 10: "Each update MAY add seeded bounded variation:
# +/-5% of max_power_kw."
VARIATION_FRACTION = 0.05


def base_power_fraction(profile_type: ProfileType, hour_of_day: int) -> float:
    """Look up the Contract's table fraction for `profile_type` at `hour_of_day` (0-23).

    FAULTY batteries use a normal profile as their base -- Contract section 10:
    "FAULTY batteries use a normal profile plus injected abnormal behavior rather
    than an unrelated normal operating model." Fault injection itself is a later
    step, so for now FAULTY simply behaves like RESIDENTIAL.
    """
    if not 0 <= hour_of_day <= 23:
        raise ValueError(f"hour_of_day must be 0-23, got {hour_of_day}")

    lookup_profile = (
        ProfileType.RESIDENTIAL if profile_type == ProfileType.FAULTY else profile_type
    )
    for hour_range, fraction in _HOURLY_POWER_FRACTION[lookup_profile]:
        if hour_of_day in hour_range:
            return fraction
    raise AssertionError(f"no fraction defined for hour {hour_of_day}")  # table is exhaustive


def requested_power_kw(
    profile_type: ProfileType,
    max_power_kw: float,
    hour_of_day: int,
    rng: random.Random,
) -> float:
    """The power a battery following `profile_type` wants at `hour_of_day`.

    Deliberately returns just a number -- `Battery.tick()` is what turns "wants"
    into "gets" once SOC limits and max_power_kw clamping are applied.
    """
    fraction = base_power_fraction(profile_type, hour_of_day)
    variation = rng.uniform(-VARIATION_FRACTION, VARIATION_FRACTION)
    return (fraction + variation) * max_power_kw
