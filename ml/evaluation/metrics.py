"""Error metrics for comparing predicted vs. actual `minutes_to_critical`
(Roadmap 4.3, Contract section 44: MAE and RMSE).

Kept as small, pure, framework-free functions -- exactly like
`ml/features.py` -- so they can be proven against hand-computed numbers
in a test, and reused unchanged in Roadmap 4.4 to score the real trained
models against this same test set.
"""

from __future__ import annotations

import numpy as np


def mean_absolute_error(actual: np.ndarray, predicted: np.ndarray) -> float:
    """Mean of |actual - predicted|. In plain terms: on average, how many
    minutes off was the prediction -- treating "10 minutes early" and
    "10 minutes late" as an equally-sized mistake."""
    actual = np.asarray(actual, dtype=float)
    predicted = np.asarray(predicted, dtype=float)
    return float(np.mean(np.abs(actual - predicted)))


def root_mean_squared_error(actual: np.ndarray, predicted: np.ndarray) -> float:
    """Square root of the mean of (actual - predicted) ** 2. Like MAE, but
    squaring each error before averaging means a handful of very wrong
    predictions pull this number up much more than they would MAE --
    RMSE is always >= MAE, and the two are only equal when every error is
    the exact same size."""
    actual = np.asarray(actual, dtype=float)
    predicted = np.asarray(predicted, dtype=float)
    return float(np.sqrt(np.mean((actual - predicted) ** 2)))
