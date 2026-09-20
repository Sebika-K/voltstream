"""Loads the ML model artifact once, at application startup (Contract
section 46: "The backend loads the model once during application
startup. It MUST NOT reload the artifact for every request."), and gives
the rest of the backend a single place to ask "is a real trained model
currently being served, and if so, which one."

A single mutable, process-wide slot (not a database row, not per-request
state) is exactly what Contract 46 asks for: the model is loaded once when
the process starts, and whatever was (or wasn't) successfully loaded then
is what every request uses for as long as that process keeps running. A
new or updated artifact appearing on disk while the backend is already up
does NOT get picked up automatically -- that would mean reloading on some
schedule or per request, which the Contract explicitly rules out. Picking
up a new model requires restarting the backend process, the same way a
code change does.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LoadedModel:
    """A successfully loaded model artifact: the fitted scikit-learn
    `Pipeline` plus the Contract section 45 metadata that was saved
    alongside it (see `ml/training/persist_model.py`)."""

    pipeline: Any
    metadata: dict


_loaded_model: LoadedModel | None = None


def load_model_artifact(path: Path) -> LoadedModel | None:
    """Attempt to load the model artifact at `path`. Never raises -- returns
    `None` if the file doesn't exist or can't be loaded for any reason.

    Contract 46: "If the artifact cannot be loaded: application remains
    operational, prediction service uses physics baseline, response
    identifies prediction_method='baseline'." A missing file is the
    expected, common case right now (see `ml/training/persist_model.py`'s
    own docstring -- Roadmap 4.4's real, measured result was that the
    physics baseline beat every trained model, so no artifact has been
    persisted yet), not a special or erroneous one; a corrupt or
    incompatible file is handled exactly the same way -- the app keeps
    running on the baseline -- just logged with more detail so the actual
    cause is still discoverable.
    """
    if not path.exists():
        logger.info("model_artifact_not_found path=%s", path)
        return None

    try:
        import joblib

        raw = joblib.load(path)
        return LoadedModel(pipeline=raw["pipeline"], metadata=raw["metadata"])
    except Exception:
        logger.exception("model_artifact_load_failed path=%s", path)
        return None


def set_loaded_model(model: LoadedModel | None) -> None:
    """Set the process-wide loaded model. Called exactly once for real, at
    startup (`app.main`'s lifespan) -- also called directly by tests that
    want to simulate "a real ML model is currently being served" without
    needing an actual joblib file on disk (see
    `tests/test_ml_prediction.py`)."""
    global _loaded_model
    _loaded_model = model


def get_loaded_model() -> LoadedModel | None:
    """The currently loaded model, or `None` if no artifact is being
    served right now (either none was ever found, or it failed to load).
    Callers treat `None` as "use the baseline" (Contract section 46)."""
    return _loaded_model
