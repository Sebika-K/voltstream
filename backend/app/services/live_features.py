"""Live feature computation for ML-based prediction serving (Roadmap 4.5,
Contract section 41).

Deliberately a standalone reimplementation of `ml/features.py`'s math, not
an import from it -- `ml/` and `backend/` are separate components with
their own dependencies and no import relationship between them (the same
"no cross-imports between components" pattern `ml/training/build_dataset.py`
and `ml/training/persist_model.py` already use for `CRITICAL_SOC_PERCENT`).
Contract section 41 requires training and inference to share the same
*math*, not the same source file -- this module is tested against the same
kind of hand-computed examples `ml/tests/test_features.py` already uses, so
a change to one side that isn't mirrored on the other gets caught by that
side's own tests failing, rather than by silent, undetected skew between
what a model was trained on and what it's fed at prediction time.

`FeatureRow`'s field order below matches `ml/features.py`'s `FeatureRow`
field-for-field on purpose: whatever pipeline gets loaded was fit on a
pandas DataFrame with columns in that exact order, and this module's
`dataclasses.asdict(...)` (used by the caller to build a single-row
DataFrame for `pipeline.predict(...)`) preserves field declaration order,
so keeping the two dataclasses in lockstep is what keeps inference from
silently feeding the model misaligned columns.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

ROLLING_WINDOW_5M_MINUTES = 5.0
ROLLING_WINDOW_15M_MINUTES = 15.0


@dataclass(frozen=True)
class LiveFeatureRow:
    """One computed feature vector for a battery's current moment."""

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


@dataclass(frozen=True)
class _PriorReading:
    timestamp: dt.datetime
    state_of_charge: float
    power_kw: float


def _rolling_power_mean(
    prior: list[_PriorReading], as_of: dt.datetime, current_power_kw: float, minutes: float
) -> float:
    """Mean `power_kw` over the trailing `minutes`-minute window ending at
    (and including) `as_of`. `current_power_kw` -- the reading *at* `as_of`
    -- is always included explicitly rather than expected to already be
    present in `prior`, so this never divides by zero (there is always at
    least the current reading) and never risks double-counting it if the
    caller's query for "history" happened to also include the current row."""
    cutoff = as_of - dt.timedelta(minutes=minutes)
    window = [r.power_kw for r in prior if cutoff < r.timestamp < as_of]
    window.append(current_power_kw)
    return sum(window) / len(window)


def _soc_change(
    prior: list[_PriorReading], as_of: dt.datetime, current_soc: float, minutes: float
) -> float | None:
    """`current_soc` minus the state_of_charge reading closest to (but not
    after) `minutes` ago. Returns `None` if there's no reading old enough
    to compare against yet -- exactly the "insufficient history" case
    Roadmap 4.4 found and fixed on the training side (a restart, or a
    battery that only recently started reporting); the serving side must
    refuse to guess here for the same reason a training row would be
    excluded rather than given a fabricated value."""
    cutoff = as_of - dt.timedelta(minutes=minutes)
    candidates = [r for r in prior if r.timestamp <= cutoff]
    if not candidates:
        return None
    reference = max(candidates, key=lambda r: r.timestamp)
    return current_soc - reference.state_of_charge


def compute_live_features(
    *,
    history: list[tuple[dt.datetime, float, float]],
    as_of: dt.datetime,
    current_soc: float,
    current_power_kw: float,
    temperature_c: float,
    health_percent: float,
    capacity_kwh: float,
    profile_type: str,
) -> LiveFeatureRow | None:
    """Compute the full V1 feature vector for a battery's current moment.

    `history`: this battery's telemetry readings STRICTLY BEFORE `as_of`
    (never including the `as_of` reading itself -- that's supplied
    separately via `current_soc`/`current_power_kw`), as
    `(timestamp, state_of_charge, power_kw)` tuples, ideally covering at
    least the last 15 minutes. Order doesn't matter -- everything here
    filters by timestamp, not position.

    Returns `None` if `soc_change_5m` can't be computed (no reading at
    least 5 minutes old) -- the caller (`app/services/prediction_service.py`)
    treats that exactly like an ML-inference failure and falls back to the
    physics baseline (Contract section 46), rather than ever predicting
    from a partially-real feature vector.
    """
    prior = [
        _PriorReading(timestamp=ts, state_of_charge=soc, power_kw=pw) for ts, soc, pw in history
    ]

    soc_change_5m = _soc_change(prior, as_of, current_soc, ROLLING_WINDOW_5M_MINUTES)
    if soc_change_5m is None:
        return None

    return LiveFeatureRow(
        state_of_charge=current_soc,
        power_kw=current_power_kw,
        temperature_c=temperature_c,
        health_percent=health_percent,
        capacity_kwh=capacity_kwh,
        rolling_power_5m=_rolling_power_mean(prior, as_of, current_power_kw, ROLLING_WINDOW_5M_MINUTES),
        rolling_power_15m=_rolling_power_mean(prior, as_of, current_power_kw, ROLLING_WINDOW_15M_MINUTES),
        soc_change_5m=soc_change_5m,
        hour_of_day=as_of.hour,
        profile_type=profile_type,
    )
