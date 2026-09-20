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


# --- Roadmap 4.5: ML prediction with baseline fallback (Contract sections
# 45-47) -------------------------------------------------------------------
#
# Everything above this point is 4.1's baseline formula, unchanged. What
# follows sits in front of it: try a real trained model if one is loaded,
# and fall back to exactly the function above whenever ML isn't available
# or doesn't work, per Contract section 46:
#
#   "If the artifact cannot be loaded: ... prediction service uses physics
#    baseline, response identifies prediction_method='baseline'."
#   "If ML inference itself fails unexpectedly: record structured error,
#    attempt baseline prediction when possible."

import dataclasses
import logging

from app.services import model_registry
from app.services.live_features import compute_live_features

logger = logging.getLogger(__name__)

PREDICTION_METHOD_ML = "ml"


@dataclass(frozen=True)
class PredictionResult:
    """The final outcome of `compute_prediction` below -- same shape as
    `BaselinePredictionResult` on purpose, since `GET
    /api/v1/batteries/{battery_id}/prediction` (Roadmap 4.5) returns this
    same shape regardless of which method actually produced it."""

    prediction_available: bool
    predicted_minutes_to_critical: float | None
    prediction_method: str | None
    model_version: str | None
    reason: str | None


def _ml_prediction_could_apply(
    *, state_of_charge: float | None, status: str | None, critical_soc_percent: float
) -> bool:
    """A cheap pre-check, deliberately mirroring the *first three* of the
    baseline's own eligibility rules (current state exists, discharging,
    still above critical) -- not rule 4 (discharge-rate floor), which is a
    baseline-specific division-by-near-zero problem that doesn't apply to
    a trained model at all (Roadmap 4.4's own `train_models.py` notes a
    trained model "never has a can't-answer-this-row case").

    This exists purely so the backend doesn't bother fetching telemetry
    history and running inference for a battery that's charging, idle, or
    already critical -- a case where "time until critical" isn't a
    meaningful question regardless of method. It is NOT a second source of
    truth for *which reason* gets reported: whenever this returns False (or
    ML is attempted and doesn't work out), `compute_baseline_prediction`
    below is what actually determines and reports the reason, so there is
    only ever one place that decides the final "reason" string.
    """
    return (
        state_of_charge is not None
        and status == "DISCHARGING"
        and state_of_charge > critical_soc_percent
    )


def _attempt_ml_prediction(
    loaded: model_registry.LoadedModel,
    *,
    state_of_charge: float,
    power_kw: float,
    temperature_c: float | None,
    health_percent: float | None,
    capacity_kwh: float,
    profile_type: str,
    as_of,
    history: list[tuple],
) -> float | None:
    """Try to produce a real ML prediction. Returns `None` (never raises)
    for any reason inference can't happen right now -- insufficient
    telemetry history to compute the live feature vector, or the model
    itself raising during `.predict(...)` -- so the caller can fall back to
    the baseline exactly as Contract 46 requires."""
    try:
        features = compute_live_features(
            history=history,
            as_of=as_of,
            current_soc=state_of_charge,
            current_power_kw=power_kw,
            temperature_c=temperature_c if temperature_c is not None else 25.0,
            health_percent=health_percent if health_percent is not None else 100.0,
            capacity_kwh=capacity_kwh,
            profile_type=profile_type,
        )
        if features is None:
            return None

        import pandas as pd

        row = pd.DataFrame([dataclasses.asdict(features)])
        predicted = loaded.pipeline.predict(row)
        return float(predicted[0])
    except Exception:
        # Contract 46: "record structured error, attempt baseline
        # prediction when possible" -- logged here, handled by the caller.
        logger.exception("ml_inference_failed")
        return None


def compute_prediction(
    *,
    capacity_kwh: float | None,
    state_of_charge: float | None,
    power_kw: float | None,
    status: str | None,
    temperature_c: float | None,
    health_percent: float | None,
    profile_type: str | None,
    as_of,
    history: list[tuple],
    critical_soc_percent: float,
    min_discharge_power_kw: float,
) -> PredictionResult:
    """The real entry point `GET /api/v1/batteries/{battery_id}/prediction`
    calls: use a loaded ML model if one exists and this battery is in a
    state where a prediction is even meaningful, otherwise (or if ML
    doesn't work out) fall back to the physics baseline -- Contract 46's
    fallback chain, end to end.

    `history`: this battery's telemetry readings strictly before `as_of`,
    as `(timestamp, state_of_charge, power_kw)` tuples (see
    `app/services/live_features.py`'s `compute_live_features` for exactly
    how these get used). Only matters when a model is actually loaded --
    the pure baseline path below never looks at it.
    """
    loaded = model_registry.get_loaded_model()

    if loaded is not None and capacity_kwh is not None and profile_type is not None:
        if _ml_prediction_could_apply(
            state_of_charge=state_of_charge,
            status=status,
            critical_soc_percent=critical_soc_percent,
        ):
            predicted_minutes = _attempt_ml_prediction(
                loaded,
                state_of_charge=state_of_charge,  # type: ignore[arg-type]
                power_kw=power_kw,  # type: ignore[arg-type]
                temperature_c=temperature_c,
                health_percent=health_percent,
                capacity_kwh=capacity_kwh,
                profile_type=profile_type,
                as_of=as_of,
                history=history,
            )
            if predicted_minutes is not None:
                return PredictionResult(
                    prediction_available=True,
                    predicted_minutes_to_critical=predicted_minutes,
                    prediction_method=PREDICTION_METHOD_ML,
                    model_version=loaded.metadata.get("model_version"),
                    reason=None,
                )

    baseline = compute_baseline_prediction(
        capacity_kwh=capacity_kwh,
        state_of_charge=state_of_charge,
        power_kw=power_kw,
        status=status,
        critical_soc_percent=critical_soc_percent,
        min_discharge_power_kw=min_discharge_power_kw,
    )
    return PredictionResult(
        prediction_available=baseline.prediction_available,
        predicted_minutes_to_critical=baseline.predicted_minutes_to_critical,
        prediction_method=baseline.prediction_method,
        model_version=baseline.model_version,
        reason=baseline.reason,
    )
