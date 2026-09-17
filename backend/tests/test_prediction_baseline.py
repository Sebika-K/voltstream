"""Tests for the physics/rate baseline depletion prediction (Roadmap 4.1,
Contract sections 37-40).

`compute_baseline_prediction` is pure -- no database, no async -- so these
are plain synchronous tests against hand-computed expected values, the same
way `_evaluate_voltage_anomaly`'s expected-voltage formula in
`test_anomaly_detection.py` is proven against exact numbers rather than
approximations. No REST endpoint exists yet to test against (Roadmap 4.5's
job); this file is entirely about the calculation itself.

Sign convention reminder (Contract section 9): `power_kw` is negative while
discharging (`energy_change_kwh = power_kw * dt_hours` reduces stored
energy), so every eligible-discharging scenario below uses a negative
`power_kw`, matching what a real `DISCHARGING` telemetry reading would
report.
"""

from __future__ import annotations

from app.services.prediction_service import (
    PREDICTION_METHOD_BASELINE,
    REASON_ALREADY_AT_OR_BELOW_CRITICAL,
    REASON_DISCHARGE_RATE_TOO_LOW,
    REASON_NO_CURRENT_STATE,
    REASON_NOT_DISCHARGING,
    compute_baseline_prediction,
)

CRITICAL_SOC_PERCENT = 20.0
MIN_DISCHARGE_POWER_KW = 0.1


def _predict(
    *,
    capacity_kwh: float | None = 10.0,
    state_of_charge: float | None = 80.0,
    power_kw: float | None = -2.0,
    status: str | None = "DISCHARGING",
):
    return compute_baseline_prediction(
        capacity_kwh=capacity_kwh,
        state_of_charge=state_of_charge,
        power_kw=power_kw,
        status=status,
        critical_soc_percent=CRITICAL_SOC_PERCENT,
        min_discharge_power_kw=MIN_DISCHARGE_POWER_KW,
    )


def test_normal_discharging_battery_gets_a_prediction():
    # capacity=10kWh, SOC=80%, critical=20% -> usable energy = 10 * 0.6 = 6.0 kWh
    # at 2.0 kW draw -> 3.0 hours -> 180.0 minutes
    result = _predict(capacity_kwh=10.0, state_of_charge=80.0, power_kw=-2.0)

    assert result.prediction_available is True
    assert result.reason is None
    assert result.prediction_method == PREDICTION_METHOD_BASELINE
    assert result.model_version is None
    assert result.predicted_minutes_to_critical == 180.0


def test_formula_holds_for_fractional_inputs_exactly():
    # capacity=7.5kWh, SOC=53%, critical=20% -> usable = 7.5 * 0.33 = 2.475 kWh
    # at 1.25 kW draw -> 1.98 hours -> 118.8 minutes, computed exactly, not
    # approximately -- these numbers were chosen so the arithmetic has no
    # floating-point surprises to round away.
    result = _predict(capacity_kwh=7.5, state_of_charge=53.0, power_kw=-1.25)

    assert result.prediction_available is True
    assert result.predicted_minutes_to_critical == 118.8


def test_charging_battery_is_not_eligible():
    result = _predict(status="CHARGING", power_kw=2.0)

    assert result.prediction_available is False
    assert result.reason == REASON_NOT_DISCHARGING
    assert result.predicted_minutes_to_critical is None
    assert result.prediction_method is None


def test_idle_battery_is_not_eligible():
    result = _predict(status="IDLE", power_kw=0.0)

    assert result.prediction_available is False
    assert result.reason == REASON_NOT_DISCHARGING


def test_faulted_battery_is_not_eligible():
    result = _predict(status="FAULT", power_kw=-1.0)

    assert result.prediction_available is False
    assert result.reason == REASON_NOT_DISCHARGING


def test_offline_battery_is_not_eligible():
    result = _predict(status="OFFLINE", power_kw=0.0)

    assert result.prediction_available is False
    assert result.reason == REASON_NOT_DISCHARGING


def test_soc_exactly_at_critical_threshold_is_not_eligible():
    # Eligibility requires SOC strictly greater than the critical threshold
    # (Contract section 39) -- exactly at it has no future countdown left.
    result = _predict(state_of_charge=20.0, power_kw=-1.0)

    assert result.prediction_available is False
    assert result.reason == REASON_ALREADY_AT_OR_BELOW_CRITICAL


def test_soc_already_below_critical_threshold_is_not_eligible():
    result = _predict(state_of_charge=15.0, power_kw=-1.0)

    assert result.prediction_available is False
    assert result.reason == REASON_ALREADY_AT_OR_BELOW_CRITICAL


def test_zero_discharge_power_is_not_eligible():
    # status is DISCHARGING and SOC is comfortably above critical, but a
    # 0 kW draw would mean dividing by zero -- withheld rather than crashing
    # or returning an infinite estimate.
    result = _predict(power_kw=0.0)

    assert result.prediction_available is False
    assert result.reason == REASON_DISCHARGE_RATE_TOO_LOW


def test_negligible_discharge_power_below_minimum_is_not_eligible():
    # -0.05 kW has abs() below the 0.1 kW MIN_DISCHARGE_POWER_KW floor.
    result = _predict(power_kw=-0.05)

    assert result.prediction_available is False
    assert result.reason == REASON_DISCHARGE_RATE_TOO_LOW


def test_discharge_power_exactly_at_minimum_is_eligible():
    # The floor is a strict "less than" check -- exactly at the minimum
    # still counts as usable.
    result = _predict(capacity_kwh=10.0, state_of_charge=80.0, power_kw=-0.1)

    assert result.prediction_available is True
    # usable = 10 * 0.6 = 6.0 kWh; at 0.1 kW -> 60 hours -> 3600 minutes
    assert result.predicted_minutes_to_critical == 3600.0


def test_missing_current_state_is_not_eligible():
    # A registered battery that has never reported telemetry (Contract
    # section 21) has no current-state row at all -- the caller passes
    # every field through as None rather than fabricating a reading.
    result = compute_baseline_prediction(
        capacity_kwh=None,
        state_of_charge=None,
        power_kw=None,
        status=None,
        critical_soc_percent=CRITICAL_SOC_PERCENT,
        min_discharge_power_kw=MIN_DISCHARGE_POWER_KW,
    )

    assert result.prediction_available is False
    assert result.reason == REASON_NO_CURRENT_STATE
    assert result.predicted_minutes_to_critical is None
    assert result.prediction_method is None
    assert result.model_version is None
