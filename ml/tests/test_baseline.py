"""Tests for ml/evaluation/baseline.py."""

import numpy as np

from evaluation.baseline import predict_minutes_to_critical


def test_matches_the_hand_computed_4_1_example():
    # Same round-number case proven in
    # backend/tests/test_prediction_baseline.py: 10 kWh capacity, 80% SOC,
    # 2 kW discharge -> usable energy 10*0.6=6.0 kWh -> 3.0 hours -> 180.0 minutes.
    result = predict_minutes_to_critical(
        state_of_charge=np.array([80.0]),
        power_kw=np.array([-2.0]),
        capacity_kwh=np.array([10.0]),
    )
    assert result[0] == 180.0


def test_near_zero_power_becomes_nan_not_a_huge_number():
    result = predict_minutes_to_critical(
        state_of_charge=np.array([50.0]),
        power_kw=np.array([-0.05]),  # below MIN_DISCHARGE_POWER_KW (0.1)
        capacity_kwh=np.array([10.0]),
    )
    assert np.isnan(result[0])


def test_power_exactly_at_the_minimum_floor_is_still_scored():
    result = predict_minutes_to_critical(
        state_of_charge=np.array([50.0]),
        power_kw=np.array([-0.1]),  # exactly at MIN_DISCHARGE_POWER_KW
        capacity_kwh=np.array([10.0]),
    )
    assert not np.isnan(result[0])


def test_handles_a_whole_array_at_once():
    result = predict_minutes_to_critical(
        state_of_charge=np.array([80.0, 50.0]),
        power_kw=np.array([-2.0, -0.05]),
        capacity_kwh=np.array([10.0, 10.0]),
    )
    assert result[0] == 180.0
    assert np.isnan(result[1])
