"""Tests for ml/evaluation/metrics.py -- hand-computed exact numbers,
matching the same style as ml/tests/test_features.py."""

import math

import numpy as np

from evaluation.metrics import mean_absolute_error, root_mean_squared_error


def test_mae_is_exact_for_a_hand_computed_example():
    actual = np.array([10.0, 20.0, 30.0])
    predicted = np.array([12.0, 18.0, 25.0])
    # errors: 2, 2, 5 -> mean = 3.0
    assert mean_absolute_error(actual, predicted) == 3.0


def test_mae_is_zero_for_perfect_predictions():
    actual = np.array([5.0, 15.0, 42.5])
    assert mean_absolute_error(actual, actual.copy()) == 0.0


def test_mae_treats_early_and_late_predictions_the_same():
    actual = np.array([10.0, 10.0])
    predicted_high = np.array([12.0, 12.0])  # predicted 2 minutes too late
    predicted_low = np.array([8.0, 8.0])     # predicted 2 minutes too early
    assert mean_absolute_error(actual, predicted_high) == 2.0
    assert mean_absolute_error(actual, predicted_low) == 2.0


def test_rmse_is_exact_for_a_hand_computed_example():
    actual = np.array([0.0, 0.0])
    predicted = np.array([3.0, 4.0])
    # squared errors: 9, 16 -> mean = 12.5 -> sqrt(12.5)
    expected = math.sqrt(12.5)
    assert abs(root_mean_squared_error(actual, predicted) - expected) < 1e-9


def test_rmse_equals_mae_when_every_error_is_the_same_size():
    actual = np.array([10.0, 10.0, 10.0])
    predicted = np.array([13.0, 13.0, 13.0])
    assert mean_absolute_error(actual, predicted) == root_mean_squared_error(actual, predicted) == 3.0


def test_rmse_punishes_one_large_miss_harder_than_mae_does():
    actual = np.array([0.0, 0.0, 0.0, 0.0])
    predicted = np.array([1.0, 1.0, 1.0, 7.0])  # three tiny misses, one big one
    mae = mean_absolute_error(actual, predicted)
    rmse = root_mean_squared_error(actual, predicted)
    assert mae == 2.5
    assert rmse > mae
