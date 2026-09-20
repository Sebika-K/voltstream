"""Persist the model selected by Roadmap 4.4's comparison, per Contract
section 45 (Model Artifact).

Run it from the `ml/` directory:

    python training/persist_model.py

Re-runs the exact same chronological split + model comparison as
`train_models.py` (the actual functions, not a re-typed copy of the
numbers), then does exactly one of two things, honestly:

  - If an ML model won: saves it, plus Contract 45's required metadata,
    to `ml/models/depletion_model_v1.joblib`. This is the file the
    backend (Roadmap 4.5's other half) loads once at startup and serves
    predictions from.
  - If the physics baseline won -- which is Roadmap 4.4's real, measured
    result as of this writing -- saves NO artifact at all, and says so.
    Contract 46 is explicit that a missing/unloadable artifact is a
    first-class, correctly-handled case: the backend falls back to the
    baseline automatically, and its response honestly reports
    `prediction_method="baseline"`. There is nothing to work around here
    -- force-persisting a model that the measured evaluation says is
    worse than the baseline would misrepresent 4.4's own result just to
    have a file to point at.

Contract section 45's required artifact metadata:

    model_version, algorithm, features, trained_at, mae, rmse,
    critical SOC threshold, feature/preprocessing version

"Actual metric values MUST be populated only after evaluation" -- true
here by construction, since `mae`/`rmse` come straight out of
`evaluate_models`'s real scoring, never a placeholder.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import sys
from pathlib import Path

import joblib
import pandas as pd
from sklearn.pipeline import Pipeline

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from features import FeatureRow  # noqa: E402
from training.evaluate_baseline import chronological_split, load_dataset  # noqa: E402
from training.train_models import (  # noqa: E402
    DISPLAY_NAMES,
    evaluate_models,
    fit_candidate_models,
    select_best_model,
)

MODEL_ARTIFACT_PATH = Path(__file__).resolve().parents[1] / "models" / "depletion_model_v1.joblib"

# Contract section 45's own metadata fields, not duplicated from anywhere
# else in this codebase -- this is where they're first defined.
MODEL_VERSION = "v1"
FEATURE_VERSION = "v1"  # Contract 41's V1 feature set; bump this if that set ever changes

# Duplicated from `backend/app/core/config.py`'s `CRITICAL_SOC_PERCENT`
# (and from `build_dataset.py`'s own copy) on purpose -- same "no
# cross-imports between components" reasoning used everywhere else in
# this project. The artifact metadata needs to record whichever critical
# SOC threshold the model was actually trained against, independent of
# whatever the backend happens to be configured with when it later loads
# this file.
CRITICAL_SOC_PERCENT = 20.0


def build_artifact_metadata(*, algorithm: str, mae: float, rmse: float) -> dict:
    """Contract section 45's required artifact metadata, as a plain dict
    (not a dataclass) since this is exactly what gets joblib-dumped
    alongside the model -- no extra translation step between "the
    metadata" and "the thing written to disk"."""
    return {
        "model_version": MODEL_VERSION,
        "algorithm": algorithm,
        "features": [f.name for f in dataclasses.fields(FeatureRow)],
        "trained_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "mae": mae,
        "rmse": rmse,
        "critical_soc_percent": CRITICAL_SOC_PERCENT,
        "feature_version": FEATURE_VERSION,
    }


def save_model_artifact(
    pipeline: Pipeline,
    *,
    algorithm: str,
    mae: float,
    rmse: float,
    path: Path = MODEL_ARTIFACT_PATH,
) -> dict:
    """Write `{"pipeline": pipeline, "metadata": {...}}` to `path` via
    joblib -- a single dict, not two separate files, so the backend can
    never end up with a model and metadata that came from different
    training runs. Returns the metadata dict that was saved alongside it."""
    metadata = build_artifact_metadata(algorithm=algorithm, mae=mae, rmse=rmse)
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({"pipeline": pipeline, "metadata": metadata}, path)
    return metadata


def persist_selected_model(
    train: pd.DataFrame,
    test: pd.DataFrame,
    *,
    path: Path = MODEL_ARTIFACT_PATH,
) -> dict | None:
    """Fit every candidate, evaluate against `test` using the exact same
    Contract-44 selection rule as `train_models.py`, and persist whichever
    one wins -- unless that's the baseline, in which case nothing is
    written (see module docstring). Returns the saved metadata, or `None`
    if nothing was saved.

    Split out from `main` specifically so it's testable against a small
    synthetic dataset (see `ml/tests/test_persist_model.py`) without
    touching the real 1.1-million-row CSV or refitting models twice."""
    fitted = fit_candidate_models(train)
    results = evaluate_models(fitted, test)
    winner = select_best_model(results)

    if winner == "baseline":
        return None

    return save_model_artifact(
        fitted[winner],
        algorithm=winner,
        mae=results[winner]["mae_minutes"],
        rmse=results[winner]["rmse_minutes"],
        path=path,
    )


def main() -> None:
    dataset = load_dataset()
    train, test = chronological_split(dataset)
    metadata = persist_selected_model(train, test)

    print("Model persistence (Roadmap 4.5, Contract section 45)")
    print("-------------------------------------------------------")

    if metadata is None:
        print("The physics baseline is the measured winner (Roadmap 4.4) -- there is")
        print("no trained model to persist.")
        print()
        print(f"No file written. {MODEL_ARTIFACT_PATH} does not exist, and the backend")
        print("will correctly fall back to the baseline, per Contract section 46.")
        return

    print(f"Selected: {DISPLAY_NAMES[metadata['algorithm']]}")
    print()
    print(f"Saved: {MODEL_ARTIFACT_PATH}")
    print(f"Metadata: {metadata}")


if __name__ == "__main__":
    main()
