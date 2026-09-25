# VoltStream — ML Report

**Status:** published, real measured results only. Compares the physics baseline (Roadmap 4.1) against Linear Regression, Random Forest, and Gradient Boosting (HistGradientBoostingRegressor) on time-to-critical-charge prediction, using actual MAE/RMSE on a real, chronologically held-out test set. This report is the condensed, portfolio-facing version of that work, including the two false starts that came before the trustworthy result below — left in deliberately, because they're the actual evidence this comparison is honest.

## Headline result

| Model                                             | MAE (min) | RMSE (min) |
| ------------------------------------------------- | --------- | ---------- |
| **Physics baseline**                              | **44.14** | **53.14**  |
| Random Forest                                     | 45.64     | 52.00      |
| HistGradientBoostingRegressor (Gradient Boosting) | 56.64     | 65.13      |
| Linear Regression                                 | 834.68    | 872.05     |

**The physics baseline wins on MAE**, with Random Forest close behind (and edging it out on RMSE). Per the project's own rule (Implementation Contract section 44 — "model selection MUST be based on measured evaluation rather than model complexity"), the baseline is what actually gets served to every live prediction today (`prediction_method: "baseline"`) — not a fallback taken for lack of a trained model, but the real, measured winner.

## Dataset and methodology

- **1,131,812 eligible training rows**, built from 824,671+ real telemetry readings accumulated by the simulator, via `ml/training/build_dataset.py`. A row is only eligible if: its SOC is above the 20% critical threshold, the battery is actively discharging, it has a full, continuous 15-minute lookback of history, and the same battery genuinely reaches ≤20% SOC at some later point in the stored history — a reading with no real future critical event is excluded entirely rather than given a fabricated target.
- **Chronological 80/20 split, never random.** The earliest 80% of readings become train, the most recent 20% become test — required per Contract section 44, because a random row-level split would let near-duplicate readings a few seconds apart end up on both sides, making any model look far more accurate than it really is on genuinely unseen future data.
- **10 features**, shared identically between training and live serving (Contract section 41, one function used by both `ml/features.py` and the backend's `live_features.py`): state of charge, power, temperature, health percent, capacity, 5-/15-minute rolling power, 5-minute SOC change, hour of day, and profile type (one-hot encoded).
- **MAE and RMSE, both required.** MAE is the average error in minutes; RMSE penalizes large errors more heavily, so a wide gap between the two flags a few very bad predictions hiding behind an otherwise decent average.

## Getting to a trustworthy result: two false starts

This comparison didn't work on the first try, and both failures are worth keeping on record — they're what makes the final numbers above trustworthy rather than assumed.

**Attempt 1 — misleadingly good, and wrong.** The first dataset spanned only ~51 real minutes of captured telemetry. Baseline MAE looked excellent (0.23 min), and Linear Regression looked almost perfect (0.01 min) — but Random Forest and HistGradientBoostingRegressor both failed badly (MAE ~5 min). Investigating directly (not accepting the numbers at face value) found the cause: in such a narrow time window, the chronological 80/20 split put "far from critical" rows almost entirely in train and "close to critical" rows almost entirely in test — two nearly disjoint target ranges. Tree-based models predict by averaging leaf values seen during training and cannot extrapolate past the range of targets they were trained on, so they failed on test targets outside that range; Linear Regression, a straight-line formula, happened to extrapolate correctly only because the underlying physics really is close to linear. Conclusion: the dataset needed to be genuinely wider before any result from it could be trusted.

**Attempt 2 — a real bug, caught by a crash, not silently.** Widening the dataset (letting the simulator run for real hours, `caffeinate -i` to stop macOS from sleeping mid-run) produced 1,179,289 rows — and then `train_models.py` crashed: `ValueError: Input X contains NaN`. Traced to one feature column, `soc_change_5m`, in 47,477 rows. Root cause: `build_dataset.py`'s "full 15-minute history" eligibility check compared each reading to that battery's very first-ever recorded reading across _all_ simulator runs ever stored — not to whether the window right before _this_ reading was actually continuous. After a restart, a reading taken just 3 minutes after telemetry resumed could pass that check trivially (hours had passed since the battery's true first-ever reading, long ago) while genuinely lacking a real 5-minutes-ago value to compute `soc_change_5m` against. Fixed by checking the computed feature vector itself for `NaN` after the fact, rather than trying to out-guess every possible history-gap shape in advance — a general lesson: "the database has data going back N minutes" does not imply "N _continuous_ minutes of data."

**The result above** is from the corrected dataset (1,131,812 rows, ~47K broken rows correctly dropped) — genuinely wide in time, with the `NaN` bug fixed, and legitimately diverse rather than an artifact of either a narrow capture window or silently broken feature values.

## Why the baseline wins

Random Forest lands close behind the baseline (and edges it out on RMSE) — consistent with it now having seen a properly wide range of real examples to learn from. Linear Regression's failure (an average error of nearly 14 hours) is the mirror image of the tree models' attempt-1 failure: tree-based models can't extrapolate past the range of targets seen in training, so they degrade gracefully at worst; Linear Regression has no such ceiling — when later test data contains feature combinations meaningfully outside what training saw, its straight line just keeps extending, producing wildly unrealistic predictions instead of leveling off.

The physics baseline (`usable_energy ÷ discharge_rate`) has no fitting step and nothing to overfit — it's a direct physical calculation from the same live telemetry every model also sees, which is exactly why it holds up here: this is a domain where the true relationship between the inputs and time-to-critical is close enough to the physics formula that a trained model's main potential advantage — learning non-linear patterns data alone reveals — doesn't have much room to beat a formula that already encodes the real underlying physics correctly.

## What's actually served

Because the baseline is the real, measured winner, no trained model is loaded in production — `ml/training/persist_model.py` never writes a `.joblib` artifact for a model that didn't win, and the backend's fallback chain (Contract section 46) genuinely executes the baseline path on every request, confirmed end-to-end with a live `curl` against a running backend and simulator (`prediction_method: "baseline"`, `model_version: null`). See [ADR-0006](../adr/0006-ml-baseline-before-advanced-models.md) for the full engineering-decision writeup of why this is the correct, honest choice rather than a shortcoming.

## Reproducibility

`ml/data/training_dataset.csv` is gitignored (regenerable, not source) — rebuild it with `python ml/training/build_dataset.py` against a Postgres instance with accumulated telemetry, then `python ml/training/evaluate_baseline.py` and `python ml/training/train_models.py` to reproduce the table above. All three scripts and the underlying feature/eligibility logic are covered by `ml/tests/` (30+ tests as of Phase 4), including a regression test for the restart-gap `NaN` bug described above.
