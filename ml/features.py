"""Shared ML feature computation (Roadmap 4.2, Contract section 41).

The Contract is explicit about why this lives in its own module, separate
from both the training script (`ml/training/build_dataset.py`) and the
backend: "Feature generation used during training and inference MUST share
the same implementation or equivalent tested logic. This prevents
training-serving skew" -- i.e. the exact same math that builds a training
row here is what Roadmap 4.5 will call again at prediction time for a live
battery. If those two ever drifted apart (say, training used a 5-minute
rolling window but serving used a 4-minute one), the model would be making
predictions on inputs shaped differently than what it learned from, and
nothing would ever surface that as an error -- it would just quietly
predict worse. Keeping this in one place, tested on its own, is what
prevents that.

Feature set V1 (Contract section 41), computed by `compute_features_for_point`
below for a single point in time on a single battery:

    state_of_charge, power_kw, temperature_c, health_percent, capacity_kwh,
    rolling_power_5m, rolling_power_15m, soc_change_5m, hour_of_day,
    profile_type

The first four plus `capacity_kwh`/`profile_type` are just the battery's own
current reading and static metadata -- no computation needed. The
interesting ones are the three that depend on *history*:

- `rolling_power_5m` / `rolling_power_15m`: the average power draw over the
  trailing 5/15 minutes. A single instantaneous power reading is noisy;
  averaging over a short window gives the model a steadier signal of "how
  hard is this battery actually being worked right now."
- `soc_change_5m`: how much the charge level has moved in the last 5
  minutes (negative while discharging). This is the closest thing to
  "current discharge rate" that's derived purely from the SOC history
  itself, independent of what the power sensor reports.
"""

from __future__ import annotations

import dataclasses

import pandas as pd

# Contract section 41/42: the rolling windows this feature set uses, and the
# longest one of them -- 15 minutes -- is also the "required historical
# feature windows" the Contract asks eligibility to check for (see
# `ml/training/build_dataset.py`'s eligibility logic). Keeping the constant
# here, not duplicated in the training script, is the same "one source of
# truth" reasoning as the module docstring above.
ROLLING_WINDOW_5M_MINUTES = 5.0
ROLLING_WINDOW_15M_MINUTES = 15.0
MAX_HISTORY_WINDOW_MINUTES = 15.0


@dataclasses.dataclass(frozen=True)
class FeatureRow:
    """One fully-computed feature vector for a single (battery, point in
    time). Field order matches the Contract's V1 feature list exactly."""

    state_of_charge: float
    power_kw: float
    temperature_c: float
    health_percent: float
    capacity_kwh: float
    rolling_power_5m: float
    rolling_power_15m: float
    soc_change_5m: float
    hour_of_day: int
    profile_type: str


def _rolling_power_mean(history: pd.DataFrame, as_of: pd.Timestamp, minutes: float) -> float:
    """Mean `power_kw` over the trailing `minutes`-minute window ending at
    (and including) `as_of`.

    `history` must already be filtered to a single battery and sorted by
    `timestamp` ascending, and must include the row at `as_of` itself.
    """
    cutoff = as_of - pd.Timedelta(minutes=minutes)
    window = history[(history["timestamp"] > cutoff) & (history["timestamp"] <= as_of)]
    return float(window["power_kw"].mean())


def _soc_change(history: pd.DataFrame, as_of: pd.Timestamp, current_soc: float, minutes: float) -> float:
    """`current_soc` minus the state_of_charge reading closest to (but not
    after) `minutes` ago -- positive while charging, negative while
    discharging, over that window.

    Returns `float("nan")` if there's no reading old enough to compare
    against yet (e.g. this battery only started reporting a minute ago) --
    the caller decides whether that makes the point ineligible, this
    function never guesses a value.
    """
    cutoff = as_of - pd.Timedelta(minutes=minutes)
    prior = history[history["timestamp"] <= cutoff]
    if prior.empty:
        return float("nan")
    reference_soc = float(prior.iloc[-1]["state_of_charge"])
    return current_soc - reference_soc


def compute_features_for_point(
    history: pd.DataFrame,
    *,
    capacity_kwh: float,
    profile_type: str,
) -> FeatureRow:
    """Compute the full V1 feature vector for the *last* row of `history`.

    `history` must be one battery's telemetry, sorted by `timestamp`
    ascending, already trimmed to whatever lookback the caller has
    available (the training script trims to `MAX_HISTORY_WINDOW_MINUTES`;
    live inference in Roadmap 4.5 will do the same against the
    `telemetry` table). The point features are computed *for* is always
    `history`'s last row -- callers slice `history` to end exactly where
    they want a feature vector, rather than passing an index in.
    """
    current = history.iloc[-1]
    as_of = current["timestamp"]
    current_soc = float(current["state_of_charge"])

    return FeatureRow(
        state_of_charge=current_soc,
        power_kw=float(current["power_kw"]),
        temperature_c=float(current["temperature_c"]),
        health_percent=float(current["health_percent"]),
        capacity_kwh=float(capacity_kwh),
        rolling_power_5m=_rolling_power_mean(history, as_of, ROLLING_WINDOW_5M_MINUTES),
        rolling_power_15m=_rolling_power_mean(history, as_of, ROLLING_WINDOW_15M_MINUTES),
        soc_change_5m=_soc_change(history, as_of, current_soc, ROLLING_WINDOW_5M_MINUTES),
        hour_of_day=int(as_of.hour),
        profile_type=profile_type,
    )
