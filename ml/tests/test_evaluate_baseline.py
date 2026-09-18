"""Tests for ml/training/evaluate_baseline.py."""

import pandas as pd

from training.evaluate_baseline import chronological_split, evaluate


def test_chronological_split_sorts_by_time_before_splitting():
    # 10 timestamps, one second apart, deliberately shuffled out of order
    # -- exactly like the real CSV, which is grouped battery-by-battery
    # rather than globally time-sorted.
    timestamps = pd.to_datetime([f"2026-01-01 00:00:{i:02d}" for i in range(10)])
    shuffled_order = [3, 0, 9, 1, 5, 2, 8, 4, 7, 6]
    dataset = pd.DataFrame(
        {"timestamp": [timestamps[i] for i in shuffled_order], "value": shuffled_order}
    )

    train, test = chronological_split(dataset, train_fraction=0.8)

    assert len(train) == 8
    assert len(test) == 2
    # train must be the 8 EARLIEST timestamps and test the 2 LATEST,
    # regardless of what order the rows were given in.
    assert list(train["timestamp"]) == list(timestamps[:8])
    assert list(test["timestamp"]) == list(timestamps[8:])


def test_evaluate_scores_only_rows_the_baseline_can_answer():
    test = pd.DataFrame(
        [
            # baseline predicts 10*((60-20)/100)/2*60 = 120.0; actual 110.0 -> error 10
            {"state_of_charge": 60.0, "power_kw": -2.0, "capacity_kwh": 10.0, "minutes_to_critical": 110.0},
            # near-zero discharge -> baseline can't score this one at all
            {"state_of_charge": 50.0, "power_kw": -0.05, "capacity_kwh": 10.0, "minutes_to_critical": 999.0},
        ]
    )

    result = evaluate(test)

    assert result["test_rows"] == 2
    assert result["scored_rows"] == 1
    assert result["unscored_rows_near_zero_power"] == 1
    assert result["mae_minutes"] == 10.0
    assert result["rmse_minutes"] == 10.0
