"""Train and compare real ML models against the physics baseline
(Roadmap 4.4, Contract sections 43-44).

Run it from the `ml/` directory:

    python training/train_models.py

Reuses the exact same chronological 80/20 split from
`ml/training/evaluate_baseline.py` (Roadmap 4.3) -- the whole point of
building that split once, correctly, was so 4.4 could reuse it unchanged
rather than risk a second, subtly different split sneaking in a
comparison that isn't really apples-to-apples.

Required evaluation order and required metrics come straight from
Contract section 44:

    1. physics baseline             (already measured in 4.3)
    2. Linear Regression
    3. Random Forest
    4. HistGradientBoostingRegressor

    metrics: MAE, RMSE

`profile_type` is the one categorical feature (Contract section 43) --
it's one-hot encoded (`handle_unknown="ignore"`, so a profile type never
seen during training won't crash inference later); every other feature is
numeric and passed through unchanged. Preprocessing and estimator are
kept together in a single sklearn Pipeline per model (Contract 43), so
there's no way to accidentally apply one model's preprocessing to a
different model's estimator.

Model selection ("based on measured evaluation rather than model
complexity," Contract 44) uses MAE as the primary metric and RMSE only to
break an exact tie -- and explicitly allows the baseline itself to win.
There is no rule here that says ML has to beat plain math just because
it's more complex.
"""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingRegressor, RandomForestRegressor
from sklearn.linear_model import LinearRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evaluation.baseline import predict_minutes_to_critical  # noqa: E402
from evaluation.metrics import mean_absolute_error, root_mean_squared_error  # noqa: E402
from features import FeatureRow  # noqa: E402
from training.evaluate_baseline import chronological_split, load_dataset  # noqa: E402

FEATURE_COLUMNS = [f.name for f in dataclasses.fields(FeatureRow)]
CATEGORICAL_FEATURES = ["profile_type"]
NUMERIC_FEATURES = [c for c in FEATURE_COLUMNS if c not in CATEGORICAL_FEATURES]
TARGET_COLUMN = "minutes_to_critical"

RANDOM_STATE = 42  # fixed so a re-run gives the same numbers, not just "close"

DISPLAY_ORDER = ["baseline", "linear_regression", "random_forest", "hist_gradient_boosting"]
DISPLAY_NAMES = {
    "baseline": "Physics baseline (4.3)",
    "linear_regression": "Linear Regression",
    "random_forest": "Random Forest",
    "hist_gradient_boosting": "HistGradientBoostingRegressor",
}


def build_preprocessor() -> ColumnTransformer:
    """One-hot encode `profile_type` (Contract section 43); leave every
    other feature untouched."""
    return ColumnTransformer(
        transformers=[
            ("profile_type", OneHotEncoder(handle_unknown="ignore"), CATEGORICAL_FEATURES),
        ],
        remainder="passthrough",
    )


def build_candidate_models() -> dict[str, Pipeline]:
    """One sklearn Pipeline per candidate -- same preprocessing, different
    estimator each time (Contract 43: preprocessing and estimator stored
    together)."""
    return {
        "linear_regression": Pipeline(
            [("preprocess", build_preprocessor()), ("model", LinearRegression())]
        ),
        "random_forest": Pipeline(
            [
                ("preprocess", build_preprocessor()),
                ("model", RandomForestRegressor(random_state=RANDOM_STATE, n_jobs=-1)),
            ]
        ),
        "hist_gradient_boosting": Pipeline(
            [
                ("preprocess", build_preprocessor()),
                ("model", HistGradientBoostingRegressor(random_state=RANDOM_STATE)),
            ]
        ),
    }


def evaluate_baseline_on(test: pd.DataFrame) -> dict:
    """Same scoring the baseline got in 4.3, just reused here so the
    printed comparison table has it alongside the real models."""
    predicted = predict_minutes_to_critical(
        state_of_charge=test["state_of_charge"].to_numpy(),
        power_kw=test["power_kw"].to_numpy(),
        capacity_kwh=test["capacity_kwh"].to_numpy(),
    )
    actual = test[TARGET_COLUMN].to_numpy()
    scorable = ~np.isnan(predicted)
    return {
        "mae_minutes": mean_absolute_error(actual[scorable], predicted[scorable]),
        "rmse_minutes": root_mean_squared_error(actual[scorable], predicted[scorable]),
        "scored_rows": int(scorable.sum()),
    }


def fit_candidate_models(train: pd.DataFrame) -> dict[str, Pipeline]:
    """Fit every candidate model on `train` and return the fitted
    pipelines themselves (not just their scores) -- Roadmap 4.5's model
    persistence needs the actual fitted object for whichever one wins,
    not only its MAE/RMSE, so this is split out from scoring rather than
    folding fitting and scoring into one function that throws the fitted
    pipelines away."""
    x_train, y_train = train[FEATURE_COLUMNS], train[TARGET_COLUMN]
    fitted: dict[str, Pipeline] = {}
    for name, pipeline in build_candidate_models().items():
        pipeline.fit(x_train, y_train)
        fitted[name] = pipeline
    return fitted


def evaluate_models(fitted: dict[str, Pipeline], test: pd.DataFrame) -> dict[str, dict]:
    """Score every already-fitted candidate model on the FULL `test` set,
    alongside the baseline. Unlike the baseline, a trained model never has
    a "can't answer this row" case -- it always produces a number, even
    for a near-zero discharge rate -- so its row count will legitimately
    be higher than the baseline's whenever the baseline had to exclude
    any."""
    results: dict[str, dict] = {"baseline": evaluate_baseline_on(test)}

    x_test, y_test = test[FEATURE_COLUMNS], test[TARGET_COLUMN].to_numpy()

    for name, pipeline in fitted.items():
        predicted = pipeline.predict(x_test)
        results[name] = {
            "mae_minutes": mean_absolute_error(y_test, predicted),
            "rmse_minutes": root_mean_squared_error(y_test, predicted),
            "scored_rows": len(y_test),
        }

    return results


def train_and_evaluate(train: pd.DataFrame, test: pd.DataFrame) -> dict[str, dict]:
    """Fit every candidate model on `train`, score every one of them on
    `test`, alongside the baseline. Kept as its own function (rather than
    inlined into `main`) since Roadmap 4.4's tests already call this
    directly -- it's now a thin wrapper over `fit_candidate_models` +
    `evaluate_models`, which Roadmap 4.5 uses separately when it needs the
    fitted pipeline objects themselves, not just their scores."""
    fitted = fit_candidate_models(train)
    return evaluate_models(fitted, test)


def select_best_model(results: dict[str, dict]) -> str:
    """Lowest MAE wins; an exact MAE tie is broken by RMSE. `results`
    normally includes "baseline" as just another candidate -- nothing
    here favors a trained model over it."""
    return min(results, key=lambda name: (results[name]["mae_minutes"], results[name]["rmse_minutes"]))


def main() -> None:
    dataset = load_dataset()
    train, test = chronological_split(dataset)
    results = train_and_evaluate(train, test)
    winner = select_best_model(results)

    print("Model comparison (Roadmap 4.4)")
    print("--------------------------------")
    print(f"Train rows: {len(train)}   Test rows: {len(test)}")
    print()
    for key in DISPLAY_ORDER:
        r = results[key]
        marker = "  <-- selected" if key == winner else ""
        print(
            f"{DISPLAY_NAMES[key]:<32} MAE: {r['mae_minutes']:6.2f} min   "
            f"RMSE: {r['rmse_minutes']:6.2f} min   (scored {r['scored_rows']} rows){marker}"
        )

    print()
    print(f"Selected: {DISPLAY_NAMES[winner]}")


if __name__ == "__main__":
    main()
