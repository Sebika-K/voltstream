"""Depletion prediction: physics/rate baseline (Roadmap 4.1, Contract
sections 37-40).

The Contract's "prediction" feature (FR-10) estimates how much time remains
before a battery's `state_of_charge` reaches a configured critical threshold
(`settings.CRITICAL_SOC_PERCENT`). This module implements only the simplest
of the two prediction methods the Contract describes -- the "physics/rate
baseline" (section 38): assume the battery's *current* discharge rate holds
constant and project forward. A more sophisticated model is explicitly a
later roadmap item, not this one.

Deliberately not built here (see Roadmap section 8, items 4.2-4.6):
  - No REST endpoint. `GET /api/v1/batteries/{battery_id}/prediction` is
    Roadmap 4.5's job, once there's more than one prediction method to
    expose -- matching this project's established pattern of building pure
    calculation logic first and wiring up an endpoint in a later, separate
    roadmap item (see e.g. Roadmap 2.1 -> 2.2, 3.2/3.3 -> 3.4).
  - No Pydantic response schema. That belongs in `app/schemas/` alongside
    whichever roadmap item actually exposes this over HTTP.

`compute_baseline_prediction` below takes plain primitive arguments rather
than an ORM row or a database session, on purpose: it has nothing to do
with SQLAlchemy or Postgres at all, so it can be unit-tested directly with
hand-picked numbers and no database fixture, the same way this project's
other pure calculation logic is tested (see `_evaluate_voltage_anomaly`'s
expected-voltage formula in `anomaly_detection_service.py` for the same
"formula lives in one small function" shape, even though that one also
happens to touch the database for alert bookkeeping and this one doesn't).
"""

from __future__ import annotations

from dataclasses import dataclass

# Contract section 39 names these two reasons explicitly. The other two
# (`no_current_state`, `discharge_rate_too_low`) are documented extensions
# beyond the Contract's literal text, added because both situations need
# *some* explicit, honest reason string -- silently returning "not
# eligible" with no explanation would be worse than naming a reason the
# Contract didn't anticipate. This mirrors how missing current-state data
# is always handled explicitly elsewhere in this project (see
# `battery_current_state.py`'s module docstring) rather than papering over
# it.
REASON_NO_CURRENT_STATE = "no_current_state"
REASON_NOT_DISCHARGING = "battery_not_discharging"
REASON_ALREADY_AT_OR_BELOW_CRITICAL = "already_at_or_below_critical_soc"
REASON_DISCHARGE_RATE_TOO_LOW = "discharge_rate_too_low"

PREDICTION_METHOD_BASELINE = "baseline"


@dataclass(frozen=True)
class BaselinePredictionResult:
    """The outcome of a single baseline-prediction calculation.

    `prediction_available=False` always comes with a `reason` and every
    other field left `None` -- there is no partial prediction. When
    `prediction_available=True`, `reason` is `None` and the three other
    fields are populated. `model_version` is `None` for now: this baseline
    method isn't a trained model with a version to track, but the field
    exists on this result already so Roadmap 4.5's endpoint doesn't need a
    breaking schema change once a real ML method (with an actual version
    string) is added alongside it.
    """

    prediction_available: bool
    predicted_minutes_to_critical: float | None
    prediction_method: str | None
    model_version: str | None
    reason: str | None


def compute_baseline_prediction(
    *,
    capacity_kwh: float | None,
    state_of_charge: float | None,
    power_kw: float | None,
    status: str | None,
    critical_soc_percent: float,
    min_discharge_power_kw: float,
) -> BaselinePredictionResult:
    """Estimate minutes remaining until `state_of_charge` reaches
    `critical_soc_percent`, assuming today's `power_kw` discharge rate holds
    constant (Contract section 38).

    `capacity_kwh`/`state_of_charge`/`power_kw`/`status` are `| None` so a
    battery with no `battery_current_state` row at all (Contract section 21
    -- registered but never reported telemetry) can be passed through
    directly as all-`None` by the caller, rather than forcing every caller
    to branch on "does this battery have current state" before it can even
    call this function.

    Eligibility (Contract section 39), checked in order:
      1. There must be current-state data at all.
      2. `status` must be `"DISCHARGING"` -- charging, idle, faulted, or
         offline batteries aren't depleting, so "time to critical" isn't a
         meaningful question for them.
      3. `state_of_charge` must be strictly greater than
         `critical_soc_percent` -- a battery already at or below the
         critical threshold has no future countdown to predict.
      4. `abs(power_kw)` must be at least `min_discharge_power_kw` -- see
         that setting's own comment in `app/core/config.py` for why a
         near-zero discharge rate is withheld rather than projected.

    The formula itself (Contract section 38), once eligible:
        usable_energy_kwh = capacity_kwh * ((state_of_charge - critical_soc_percent) / 100)
        hours_remaining    = usable_energy_kwh / abs(power_kw)
        minutes_remaining  = hours_remaining * 60

    `power_kw` is negative while discharging (Contract section 9's sign
    convention: `energy_change_kwh = power_kw * dt_hours`, so a negative
    `power_kw` reduces stored energy) -- `abs()` here converts that signed
    rate into a plain magnitude for the division.
    """
    if capacity_kwh is None or state_of_charge is None or power_kw is None or status is None:
        return BaselinePredictionResult(
            prediction_available=False,
            predicted_minutes_to_critical=None,
            prediction_method=None,
            model_version=None,
            reason=REASON_NO_CURRENT_STATE,
        )

    if status != "DISCHARGING":
        return BaselinePredictionResult(
            prediction_available=False,
            predicted_minutes_to_critical=None,
            prediction_method=None,
            model_version=None,
            reason=REASON_NOT_DISCHARGING,
        )

    if state_of_charge <= critical_soc_percent:
        return BaselinePredictionResult(
            prediction_available=False,
            predicted_minutes_to_critical=None,
            prediction_method=None,
            model_version=None,
            reason=REASON_ALREADY_AT_OR_BELOW_CRITICAL,
        )

    if abs(power_kw) < min_discharge_power_kw:
        return BaselinePredictionResult(
            prediction_available=False,
            predicted_minutes_to_critical=None,
            prediction_method=None,
            model_version=None,
            reason=REASON_DISCHARGE_RATE_TOO_LOW,
        )

    usable_energy_kwh = capacity_kwh * ((state_of_charge - critical_soc_percent) / 100)
    hours_remaining = usable_energy_kwh / abs(power_kw)
    minutes_remaining = hours_remaining * 60

    return BaselinePredictionResult(
        prediction_available=True,
        predicted_minutes_to_critical=minutes_remaining,
        prediction_method=PREDICTION_METHOD_BASELINE,
        model_version=None,
        reason=None,
    )
