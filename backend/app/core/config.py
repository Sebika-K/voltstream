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


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide Settings singleton (cached after first construction)."""
    return Settings()
