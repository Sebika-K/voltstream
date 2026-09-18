"""Physics-baseline prediction, reimplemented standalone in `ml/` for
evaluation purposes (Roadmap 4.3, TDD section 19.1).

This intentionally duplicates the formula in
`backend/app/services/prediction_service.py` rather than importing it --
same reasoning as `CRITICAL_SOC_PERCENT` in `ml/training/build_dataset.py`:
`ml/` has no dependency on the backend package at all, by design (it's a
standalone, separately-run pipeline). Reconciling the two into one shared
implementation is deferred to Roadmap 4.5, when the backend actually needs
to serve live predictions and this evaluation code becomes directly
relevant to what gets served.
"""

from __future__ import annotations

import numpy as np

# Same values as backend/app/core/config.py's CRITICAL_SOC_PERCENT and
# MIN_DISCHARGE_POWER_KW (Roadmap 4.1).
CRITICAL_SOC_PERCENT = 20.0
MIN_DISCHARGE_POWER_KW = 0.1


def predict_minutes_to_critical(
    *,
    state_of_charge: np.ndarray,
    power_kw: np.ndarray,
    capacity_kwh: np.ndarray,
) -> np.ndarray:
    """Vectorized version of the exact 4.1 formula, applied to a whole
    array of rows at once instead of one battery at a time:

        usable_energy_kwh = capacity_kwh * ((state_of_charge - CRITICAL_SOC_PERCENT) / 100)
        minutes_remaining = (usable_energy_kwh / abs(power_kw)) * 60

    A row whose discharge rate is below MIN_DISCHARGE_POWER_KW gets `NaN`
    instead of a huge or infinite number -- the baseline genuinely has no
    good answer for "how long until critical" when a battery is barely
    discharging at all, so it says so rather than guessing. Those rows are
    excluded from the MAE/RMSE calculation entirely, not scored as wrong.
    """
    state_of_charge = np.asarray(state_of_charge, dtype=float)
    power_kw = np.asarray(power_kw, dtype=float)
    capacity_kwh = np.asarray(capacity_kwh, dtype=float)

    usable_energy_kwh = capacity_kwh * ((state_of_charge - CRITICAL_SOC_PERCENT) / 100.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        minutes_remaining = (usable_energy_kwh / np.abs(power_kw)) * 60.0

    too_low_power = np.abs(power_kw) < MIN_DISCHARGE_POWER_KW
    return np.where(too_low_power, np.nan, minutes_remaining)
