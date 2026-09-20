"""Tests for `compute_prediction`'s ML + baseline fallback chain (Roadmap
4.5, Contract section 46).

Uses a small fake "pipeline" object instead of a real scikit-learn model --
this suite is about the FALLBACK LOGIC (when ML gets tried, when and why
it falls back, what the caller ends up seeing), not scikit-learn itself,
so a plain stand-in with a `.predict(...)` method is all that's needed.
"""

from __future__ import annotations

import datetime as dt

from app.services import model_registry
from app.services.prediction_service import (
    PREDICTION_METHOD_BASELINE,
    PREDICTION_METHOD_ML,
    REASON_NOT_DISCHARGING,
    compute_prediction,
)

AS_OF = dt.datetime(2026, 1, 1, 14, 30, 0, tzinfo=dt.timezone.utc)
CRITICAL_SOC_PERCENT = 20.0
MIN_DISCHARGE_POWER_KW = 0.1


class _FakePipeline:
    """Stands in for a fitted scikit-learn `Pipeline`: `.predict(dataframe)`
    returns whatever single value the test configured, or raises whatever
    exception it configured -- no scikit-learn needs to be installed for
    this file to run at all."""

    def __init__(self, *, returns: float | None = None, raises: Exception | None = None):
        self._returns = returns
        self._raises = raises
        self.calls: list = []

    def predict(self, dataframe):
        self.calls.append(dataframe)
        if self._raises is not None:
            raise self._raises
        return [self._returns]


def teardown_function(_):
    model_registry.set_loaded_model(None)  # never leak a fake model into another test


def _history_with_enough_lookback():
    return [
        (AS_OF - dt.timedelta(minutes=6), 60.0, -2.0),
        (AS_OF - dt.timedelta(minutes=2), 55.0, -2.5),
    ]


def _predict(*, history, status="DISCHARGING", state_of_charge=50.0, power_kw=-2.0):
    return compute_prediction(
        capacity_kwh=10.0,
        state_of_charge=state_of_charge,
        power_kw=power_kw,
        status=status,
        temperature_c=25.0,
        health_percent=98.0,
        profile_type="RESIDENTIAL",
        as_of=AS_OF,
        history=history,
        critical_soc_percent=CRITICAL_SOC_PERCENT,
        min_discharge_power_kw=MIN_DISCHARGE_POWER_KW,
    )


def test_no_model_loaded_falls_straight_to_the_baseline():
    model_registry.set_loaded_model(None)

    result = _predict(history=_history_with_enough_lookback())

    assert result.prediction_available is True
    assert result.prediction_method == PREDICTION_METHOD_BASELINE
    assert result.model_version is None


def test_a_loaded_model_is_used_when_it_can_answer():
    fake = _FakePipeline(returns=137.0)
    model_registry.set_loaded_model(
        model_registry.LoadedModel(pipeline=fake, metadata={"model_version": "v1"})
    )

    result = _predict(history=_history_with_enough_lookback())

    assert result.prediction_available is True
    assert result.prediction_method == PREDICTION_METHOD_ML
    assert result.model_version == "v1"
    assert result.predicted_minutes_to_critical == 137.0
    assert len(fake.calls) == 1


def test_falls_back_to_baseline_when_history_is_too_short_for_ml_features():
    fake = _FakePipeline(returns=999.0)
    model_registry.set_loaded_model(
        model_registry.LoadedModel(pipeline=fake, metadata={"model_version": "v1"})
    )

    result = _predict(history=[])  # nothing old enough to compute soc_change_5m

    assert result.prediction_method == PREDICTION_METHOD_BASELINE
    assert fake.calls == []  # ML was never even attempted


def test_falls_back_to_baseline_when_inference_itself_raises():
    fake = _FakePipeline(raises=ValueError("model blew up"))
    model_registry.set_loaded_model(
        model_registry.LoadedModel(pipeline=fake, metadata={"model_version": "v1"})
    )

    result = _predict(history=_history_with_enough_lookback())

    assert result.prediction_available is True
    assert result.prediction_method == PREDICTION_METHOD_BASELINE
    assert result.model_version is None  # the baseline's own None, not the failed model's


def test_a_charging_battery_never_attempts_ml_and_is_correctly_unavailable():
    fake = _FakePipeline(returns=42.0)
    model_registry.set_loaded_model(
        model_registry.LoadedModel(pipeline=fake, metadata={"model_version": "v1"})
    )

    result = _predict(history=_history_with_enough_lookback(), status="CHARGING")

    assert result.prediction_available is False
    assert result.reason == REASON_NOT_DISCHARGING
    assert fake.calls == []
