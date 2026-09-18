"""Evaluate the physics baseline against the real training dataset
(Roadmap 4.3, Contract section 44).

Run it from the `ml/` directory:

    python training/evaluate_baseline.py

Loads `ml/data/training_dataset.csv` (built by `build_dataset.py`,
Roadmap 4.2), sorts it chronologically across the *whole fleet* (not per
battery), and splits it 80/20 by time: the earliest 80% of readings become
"train", the most recent 20% become "test". The physics baseline doesn't
actually train on anything, but this exact split gets reused unchanged in
Roadmap 4.4, so it's built here once, correctly, rather than twice.

Contract section 44 is explicit that this MUST be a chronological split,
never a random row-level one: telemetry rows are a time series, and two
readings taken seconds apart from the same battery's same discharge event
are extremely similar. A random split would let some of those near-
duplicate moments end up in "train" and others in "test", making any
model (or the baseline) look far more accurate than it will actually be
on a battery it hasn't already partly seen.

The baseline formula itself lives in `ml/evaluation/baseline.py`,
reimplemented standalone rather than imported from the backend -- see
that file's docstring for why. The MAE/RMSE math lives in
`ml/evaluation/metrics.py`.

Prints how many test rows the baseline could and couldn't score (a
near-zero discharge rate can't be scored at all -- see
`evaluation/baseline.py`), plus the MAE and RMSE over the rows it could
score, so the result is never a silently misleading single number.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evaluation.baseline import predict_minutes_to_critical  # noqa: E402
from evaluation.metrics import mean_absolute_error, root_mean_squared_error  # noqa: E402

DATASET_PATH = Path(__file__).resolve().parents[1] / "data" / "training_dataset.csv"
TRAIN_FRACTION = 0.8


def chronological_split(
    dataset: pd.DataFrame, train_fraction: float = TRAIN_FRACTION
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Sort by timestamp ascending (regardless of what order the rows
    arrived in -- `build_dataset.py` writes them grouped battery-by-
    battery, not globally time-ordered) and cut the first `train_fraction`
    of rows into "train", the rest into "test"."""
    dataset = dataset.sort_values("timestamp", kind="mergesort").reset_index(drop=True)
    split_index = int(len(dataset) * train_fraction)
    return dataset.iloc[:split_index], dataset.iloc[split_index:]


def load_dataset(dataset_path: Path = DATASET_PATH) -> pd.DataFrame:
    return pd.read_csv(dataset_path, parse_dates=["timestamp"])


def evaluate(test: pd.DataFrame) -> dict:
    predicted = predict_minutes_to_critical(
        state_of_charge=test["state_of_charge"].to_numpy(),
        power_kw=test["power_kw"].to_numpy(),
        capacity_kwh=test["capacity_kwh"].to_numpy(),
    )
    actual = test["minutes_to_critical"].to_numpy()

    scorable = ~np.isnan(predicted)
    unscored_count = int((~scorable).sum())

    mae = mean_absolute_error(actual[scorable], predicted[scorable])
    rmse = root_mean_squared_error(actual[scorable], predicted[scorable])

    return {
        "test_rows": len(test),
        "scored_rows": int(scorable.sum()),
        "unscored_rows_near_zero_power": unscored_count,
        "mae_minutes": mae,
        "rmse_minutes": rmse,
    }


def main() -> None:
    dataset = load_dataset()
    train, test = chronological_split(dataset)
    results = evaluate(test)

    print("Baseline evaluation (Roadmap 4.3)")
    print("----------------------------------")
    print(f"Dataset rows total:                    {len(train) + len(test)}")
    print(f"Train rows (first 80%, unused here):   {len(train)}")
    print(f"Test rows (last 20%):                  {results['test_rows']}")
    print(f"  scored:                               {results['scored_rows']}")
    print(f"  excluded (near-zero discharge rate):  {results['unscored_rows_near_zero_power']}")
    print(f"MAE:  {results['mae_minutes']:.2f} minutes")
    print(f"RMSE: {results['rmse_minutes']:.2f} minutes")


if __name__ == "__main__":
    main()
