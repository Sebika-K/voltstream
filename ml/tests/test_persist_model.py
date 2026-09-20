"""Tests for ml/training/persist_model.py (Roadmap 4.5, Contract section 45).

Two small synthetic datasets, not the real CSV: one shaped so a trained
model legitimately wins (proving the save path actually writes a loadable
artifact with the right metadata), one shaped so the physics baseline
wins (proving persist_selected_model correctly writes nothing rather than
saving a model that lost)."""

from __future__ import annotations

import dataclasses

import joblib
import numpy as np
import pandas as pd

from features import FeatureRow
from training.persist_model import (
    CRITICAL_SOC_PERCENT,
    build_artifact_metadata,
    persist_selected_model,
    save_model_artifact,
)
from training.train_models import build_candidate_models

FEATURE_COLUMNS = [f.name for f in dataclasses.fields(FeatureRow)]


def _base_frame(n: int, rng: np.random.Generator) -> pd.DataFrame:
    """Every FeatureRow column filled with plausible values, plus a
    `minutes_to_critical` target -- shared scaffolding for both datasets
    below, which differ only in what the target actually depends on."""
    return pd.DataFrame(
        {
            "state_of_charge": rng.uniform(20.0001, 100.0, n),
            "power_kw": -rng.uniform(0.5, 5.0, n),
            "temperature_c": rng.uniform(15.0, 35.0, n),
            "health_percent": rng.uniform(90.0, 100.0, n),
            "capacity_kwh": np.full(n, 10.0),
            "rolling_power_5m": -rng.uniform(0.5, 5.0, n),
            "rolling_power_15m": -rng.uniform(0.5, 5.0, n),
            "soc_change_5m": -rng.uniform(0.1, 2.0, n),
            "hour_of_day": rng.integers(0, 24, n),
            "profile_type": rng.choice(["RESIDENTIAL", "SOLAR", "COMMERCIAL"], n),
        }
    )


def _dataset_where_a_model_should_win(n: int = 400) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Target is `2 * state_of_charge`, with no relationship at all to the
    physics baseline's formula (which uses power_kw/capacity_kwh, not a
    flat multiple of state_of_charge alone) -- Linear Regression fits this
    almost perfectly, while the baseline's formula-based guess is
    systematically wrong. A model winning here is a real, expected result,
    not a fluke."""
    rng = np.random.default_rng(1)
    train = _base_frame(n, rng)
    train["minutes_to_critical"] = 2.0 * train["state_of_charge"]
    test = _base_frame(n, rng)
    test["minutes_to_critical"] = 2.0 * test["state_of_charge"]
    return train, test


def _dataset_where_the_baseline_should_win(n: int = 200) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Target is the exact physics-baseline formula itself -- the baseline
    scores ~0 error by construction, which no amount of ML training can
    beat, so `select_best_model` must pick "baseline" every time."""
    rng = np.random.default_rng(2)

    def _frame(k: int) -> pd.DataFrame:
        frame = _base_frame(k, rng)
        frame["minutes_to_critical"] = (
            frame["capacity_kwh"]
            * ((frame["state_of_charge"] - CRITICAL_SOC_PERCENT) / 100.0)
            / frame["power_kw"].abs()
            * 60.0
        )
        return frame

    return _frame(n), _frame(n)


def test_build_artifact_metadata_has_every_contract_45_field():
    metadata = build_artifact_metadata(algorithm="random_forest", mae=1.23, rmse=4.56)

    assert metadata["model_version"] == "v1"
    assert metadata["algorithm"] == "random_forest"
    assert metadata["features"] == FEATURE_COLUMNS
    assert metadata["mae"] == 1.23
    assert metadata["rmse"] == 4.56
    assert metadata["critical_soc_percent"] == CRITICAL_SOC_PERCENT
    assert metadata["feature_version"] == "v1"
    # trained_at is a real, parseable timestamp, not a placeholder string.
    from datetime import datetime

    datetime.fromisoformat(metadata["trained_at"])


def test_save_model_artifact_round_trips_through_joblib(tmp_path):
    train, _ = _dataset_where_a_model_should_win(n=50)
    pipeline = build_candidate_models()["linear_regression"]
    pipeline.fit(train[FEATURE_COLUMNS], train["minutes_to_critical"])

    path = tmp_path / "depletion_model_v1.joblib"
    metadata = save_model_artifact(pipeline, algorithm="linear_regression", mae=0.5, rmse=0.7, path=path)

    assert path.exists()
    loaded = joblib.load(path)
    assert loaded["metadata"] == metadata
    # The loaded pipeline is genuinely usable, not just present.
    predictions = loaded["pipeline"].predict(train[FEATURE_COLUMNS].head(5))
    assert len(predictions) == 5


def test_persist_selected_model_saves_a_real_artifact_when_a_model_wins(tmp_path):
    train, test = _dataset_where_a_model_should_win()
    path = tmp_path / "depletion_model_v1.joblib"

    metadata = persist_selected_model(train, test, path=path)

    assert metadata is not None
    assert path.exists()
    assert metadata["algorithm"] in {"linear_regression", "random_forest", "hist_gradient_boosting"}
    assert metadata["mae"] < 5.0  # a model should fit this near-perfectly-learnable target well

    loaded = joblib.load(path)
    assert loaded["metadata"] == metadata


def test_persist_selected_model_writes_nothing_when_the_baseline_wins(tmp_path):
    train, test = _dataset_where_the_baseline_should_win()
    path = tmp_path / "depletion_model_v1.joblib"

    metadata = persist_selected_model(train, test, path=path)

    assert metadata is None
    assert not path.exists()
