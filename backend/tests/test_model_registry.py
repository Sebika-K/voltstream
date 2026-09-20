"""Tests for the model-loading singleton (Roadmap 4.5, Contract section 46).

`load_model_artifact` never raises -- it always returns either a real
`LoadedModel` or `None`, so the app can stay operational no matter what's
(or isn't) on disk. `set_loaded_model`/`get_loaded_model` are the plain
get/set pair the rest of the backend (and these tests) use instead of
touching the module-level variable directly.
"""

from __future__ import annotations

from pathlib import Path

import joblib

from app.services.model_registry import (
    LoadedModel,
    get_loaded_model,
    load_model_artifact,
    set_loaded_model,
)


def test_missing_file_returns_none_not_an_exception(tmp_path: Path):
    missing = tmp_path / "does_not_exist.joblib"
    assert load_model_artifact(missing) is None


def test_a_real_artifact_loads_into_pipeline_and_metadata(tmp_path: Path):
    path = tmp_path / "depletion_model_v1.joblib"
    joblib.dump({"pipeline": "a-fake-pipeline-object", "metadata": {"model_version": "v1"}}, path)

    loaded = load_model_artifact(path)

    assert loaded is not None
    assert loaded.pipeline == "a-fake-pipeline-object"
    assert loaded.metadata == {"model_version": "v1"}


def test_a_corrupt_file_returns_none_not_an_exception(tmp_path: Path):
    path = tmp_path / "corrupt.joblib"
    path.write_bytes(b"this is not a real joblib file")

    assert load_model_artifact(path) is None


def test_a_file_with_the_wrong_shape_returns_none(tmp_path: Path):
    # A real joblib file, but missing the "pipeline"/"metadata" keys
    # `load_model_artifact` expects -- still handled gracefully, not a
    # crash, since a hand-edited or future-format artifact shouldn't be
    # able to take the whole app down.
    path = tmp_path / "wrong_shape.joblib"
    joblib.dump({"unexpected": "shape"}, path)

    assert load_model_artifact(path) is None


def test_set_and_get_loaded_model_round_trip():
    model = LoadedModel(pipeline="stub", metadata={"model_version": "v1"})
    set_loaded_model(model)
    try:
        assert get_loaded_model() is model
    finally:
        set_loaded_model(None)  # never leak state into a later test
    assert get_loaded_model() is None
