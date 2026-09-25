# ADR-0006: Simple ML baseline before advanced models

**Status:** Accepted

## Context

VoltStream predicts time-to-critical-charge for a discharging battery (Contract sections
37-40). TDD constraint #7 requires "measured evidence before introducing optimization
infrastructure," and the Roadmap's Phase 4 sequence is explicit about order: build and
evaluate a physics baseline (4.1, 4.3) *before* training or comparing any ML model
(4.4), rather than assuming a trained model would obviously do better.

## Decision

Implement the simplest possible prediction first — a physics/rate baseline that assumes
the battery's current discharge rate holds constant and projects forward
(`compute_baseline_prediction`) — evaluate it for real, and only then train and compare
ML models (Linear Regression, Random Forest, Gradient Boosting) against that baseline on
the same real, held-out data. Serve whichever one actually measures better, with
automatic fallback to the baseline if no model is loaded or a loaded model can't answer
a given request (`app/services/model_registry.py`, `prediction_service.py`).

## Alternatives considered

- **Train an ML model first and assume it beats a naive baseline.** Rejected — this is
  exactly the "optimization without measured evidence" pattern the TDD's constraint #7
  and section 61's "no optimization is considered demonstrated without baseline →
  hypothesis → implementation change → equivalent benchmark → comparison" rule exist to
  prevent.
- **Skip the baseline and go straight to the "best" model architecture available.**
  Rejected — without a baseline there is nothing to measure improvement against, and (as
  this project's own results below show) the assumption that a more complex model wins
  is not automatically true.

## Consequences

Positive — the outcome vindicated the process, not just the choice:

- An early baseline evaluation (Roadmap 4.3) on a small dataset showed a suspiciously
  good MAE of 0.23 minutes / RMSE of 0.31 minutes — later diagnosed (4.4) as an artifact
  of too little data diversity, not real predictive accuracy. Following the "measure
  before trusting" discipline is what caught this rather than shipping a falsely
  confident number.
- On a genuinely wide, real dataset (1,131,812 rows), the actual measured result is: the
  physics baseline wins outright — MAE 44.14 min / RMSE 53.14 min — edging out Random
  Forest (45.64 / 52.00) and clearly beating HistGradientBoostingRegressor (56.64 /
  65.13) and Linear Regression (834.68 / 872.05, which fails badly by extrapolating far
  outside its training range). This is a legitimate, measured outcome, not an assumption
  — and it means the baseline the project started with is also the model that actually
  serves every live prediction today.
- Because the baseline is the real winner, no trained-model artifact was ever persisted
  or force-loaded to make the project look more "ML-heavy" than the data justifies — the
  serving/fallback machinery was built for real (Roadmap 4.5) and is fully exercised, it
  just currently resolves to the baseline every time, which is itself the honest result
  the Contract's rule ("model choice must follow the numbers, not assumed complexity")
  asks for.

Negative / accepted trade-offs:

- The project's ML story is smaller than "we trained and deployed a model" — it's "we
  trained several, measured them honestly, and the simple method won." For a portfolio
  project, this is a feature, not a bug: it demonstrates evaluation discipline rather
  than a specific model architecture, and the full comparison (with real MAE/RMSE
  numbers) is reproducible from the same training pipeline (Roadmap 4.2's dataset
  generation) if the underlying data or model set changes.
