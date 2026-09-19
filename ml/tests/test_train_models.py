"""Tests for ml/training/train_models.py."""

import math

import numpy as np
import pandas as pd

from training.train_models import select_best_model, train_and_evaluate


def test_select_best_model_picks_the_lowest_mae():
    results = {
        "baseline": {"mae_minutes": 5.0, "rmse_minutes": 7.0},
        "linear_regression": {"mae_minutes": 3.0, "rmse_minutes": 4.0},
        "random_forest": {"mae_minutes": 2.0, "rmse_minutes": 6.0},
        "hist_gradient_boosting": {"mae_minutes": 4.0, "rmse_minutes": 4.5},
    }
    assert select_best_model(results) == "random_forest"


def test_select_best_model_breaks_an_exact_mae_tie_with_rmse():
    results = {
        "baseline": {"mae_minutes": 2.0, "rmse_minutes": 3.0},
        "linear_regression": {"mae_minutes": 2.0, "rmse_minutes": 2.5},
    }
    assert select_best_model(results) == "linear_regression"


def test_select_best_model_can_pick_the_baseline_over_every_ml_model():
    # Exactly what actually happened in 4.3 -- the baseline is allowed to
    # win. Nothing here favors a "fancier" model just for being fancier.
    results = {
        "baseline": {"mae_minutes": 0.23, "rmse_minutes": 0.31},
        "linear_regression": {"mae_minutes": 5.1, "rmse_minutes": 6.0},
        "random_forest": {"mae_minutes": 1.8, "rmse_minutes": 2.4},
        "hist_gradient_boosting": {"mae_minutes": 1.2, "rmse_minutes": 1.9},
    }
    assert select_best_model(results) == "baseline"


def _synthetic_dataset(n: int) -> pd.DataFrame:
    """A small, self-contained dataset shaped exactly like the real one
    (same columns), with a genuinely learnable target -- not just noise --
    so this test proves the whole pipeline actually runs end to end, not
    just that it doesn't crash on garbage."""
    rng = np.random.default_rng(0)
    soc = rng.uniform(20.0001, 100.0, n)
    power = -rng.uniform(0.5, 5.0, n)
    capacity = np.full(n, 10.0)
    target = capacity * ((soc - 20.0) / 100.0) / np.abs(power) * 60.0
    return pd.DataFrame(
        {
            "battery_id": [f"BAT-{i:04d}" for i in range(n)],
            "timestamp": pd.date_range("2026-01-01", periods=n, freq="min"),
            "state_of_charge": soc,
            "power_kw": power,
            "temperature_c": rng.uniform(20.0, 35.0, n),
            "health_percent": rng.uniform(95.0, 100.0, n),
            "capacity_kwh": capacity,
            "rolling_power_5m": power,
            "rolling_power_15m": power,
            "soc_change_5m": rng.uniform(-2.0, 2.0, n),
            "hour_of_day": rng.integers(0, 24, n),
            "profile_type": rng.choice(["RESIDENTIAL", "SOLAR", "COMMERCIAL"], n),
            "minutes_to_critical": target,
        }
    )


def test_train_and_evaluate_returns_all_four_entries_with_sane_metrics():
    dataset = _synthetic_dataset(120)
    train, test = dataset.iloc[:96], dataset.iloc[96:]

    results = train_and_evaluate(train, test)

    assert set(results) == {"baseline", "linear_regression", "random_forest", "hist_gradient_boosting"}
    for name, r in results.items():
        assert math.isfinite(r["mae_minutes"]), name
        assert math.isfinite(r["rmse_minutes"]), name
        assert r["mae_minutes"] >= 0
        # RMSE is mathematically always >= MAE (see ml/tests/test_metrics.py)
        assert r["rmse_minutes"] >= r["mae_minutes"] - 1e-9
