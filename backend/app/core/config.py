"""Application configuration.

Settings are sourced from environment variables (and a local ``.env`` file when present),
per the VoltStream configuration contract: configuration comes primarily from the
environment, secrets are never hard-coded, and missing required values must cause an
explicit startup failure rather than a silent fallback.

Only foundation-level settings live here for now (application identity and database
connectivity/pooling). Domain-specific configuration -- telemetry, alert thresholds,
prediction, simulator behavior, etc. -- will be added alongside the features that need it.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed application settings.

    ``DATABASE_URL`` has no default: if it is not supplied via the environment or a
    ``.env`` file, Pydantic raises a ``ValidationError`` at startup instead of the
    application silently running without a usable database configuration.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Application identity
    APP_NAME: str = "VoltStream Backend"
    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "INFO"

    # Database connectivity (required -- see docstring above)
    DATABASE_URL: str

    # Connection-pool behavior is configurable rather than fixed, per the TDD.
    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 10
    DB_POOL_TIMEOUT_SECONDS: float = 30.0

    # Bound on how long a single readiness probe may take before it is treated as a failure.
    DB_READY_TIMEOUT_SECONDS: float = 2.0

    # Roadmap 1.9 / Contract sections 16 + 54: the hard ceiling on how many events a
    # single POST /api/v1/telemetry/batch request may contain. Exceeding it is a
    # payload-size problem (413), not a malformed-data problem (422) -- see
    # app/api/telemetry.py for where this gets enforced.
    MAX_BATCH_SIZE: int = 1000

    # Roadmap 3.2 / Contract section 22: a battery counts as offline once this
    # many seconds pass with no new telemetry. The Contract's default formula is
    # `max(10, 3 * telemetry_interval)`; this project's simulator sends telemetry
    # roughly once per second per device (Phase 1), so `3 * telemetry_interval`
    # (~3s) is smaller than the 10-second floor -- the floor is what actually
    # applies here, hence the plain default below rather than a derived one.
    OFFLINE_THRESHOLD_SECONDS: float = 10.0

    # How often the background offline-detection loop re-checks every battery's
    # `last_seen` against the threshold above (Contract section 22: "runs
    # periodically outside the primary telemetry request path"). Not itself
    # part of the Contract's formula -- a deliberate, documented choice: frequent
    # enough that stopping a simulated battery becomes visible within a handful
    # of seconds, without re-scanning the table needlessly often.
    OFFLINE_DETECTION_INTERVAL_SECONDS: float = 5.0

    # Roadmap 3.3 / Contract section 25: rule-based anomaly detection
    # thresholds. All "VoltStream simulation/monitoring defaults" per the
    # Contract's own framing (section 2) -- not real battery-safety limits.
    LOW_SOC_WARNING_PERCENT: float = 20.0
    LOW_SOC_CRITICAL_PERCENT: float = 10.0
    HIGH_TEMPERATURE_WARNING_C: float = 50.0
    HIGH_TEMPERATURE_CRITICAL_C: float = 55.0
    # Rapid discharge is evaluated over a rolling window: how far back to look
    # for a comparison SOC reading, and how many percentage points of decline
    # across that window count as WARNING/CRITICAL.
    RAPID_DISCHARGE_WINDOW_MINUTES: float = 5.0
    RAPID_DISCHARGE_WARNING_PERCENTAGE_POINTS: float = 5.0
    RAPID_DISCHARGE_CRITICAL_PERCENTAGE_POINTS: float = 10.0
    # Percent deviation between measured voltage and the simulator's expected
    # voltage (Contract section 11's formula), not an absolute volt figure.
    VOLTAGE_ANOMALY_WARNING_PERCENT: float = 7.5
    VOLTAGE_ANOMALY_CRITICAL_PERCENT: float = 12.5

    # Roadmap 4.1 / Contract sections 37-39: depletion-prediction baseline.
    # `CRITICAL_SOC_PERCENT` is conceptually distinct from
    # `LOW_SOC_CRITICAL_PERCENT` above even though both are "critical SOC"
    # numbers -- that one drives the LOW_SOC *alert* (Roadmap 3.3); this one
    # is the target SOC the depletion prediction counts down to (Contract
    # section 39: "prediction estimates time until state_of_charge reaches
    # this configured critical threshold"). They happen to share a default
    # value in this project, but changing one must not silently change the
    # other, hence two separate settings rather than one shared constant.
    CRITICAL_SOC_PERCENT: float = 20.0
    # Contract section 39: a battery reporting `status="DISCHARGING"` with
    # a negligible discharge rate (near 0 kW) is technically discharging but
    # not usefully predictable -- dividing by a near-zero power draw would
    # produce a wildly large (and meaningless) time-to-critical estimate.
    # Below this magnitude of power draw, the prediction is withheld
    # entirely rather than returned as a huge, misleading number.
    MIN_DISCHARGE_POWER_KW: float = 0.1


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide Settings singleton (cached after first construction)."""
    return Settings()
