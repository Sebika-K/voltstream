"""Simulator configuration (Roadmap 1.10, Contract section 54).

Configuration comes from environment variables, per the project-wide
configuration contract: nothing hard-coded, and an invalid value should fail
loudly rather than silently falling back to something else. This stays a
plain dataclass reading `os.environ` -- the same style `FleetConfig` (fleet.py)
already uses -- rather than pulling in pydantic-settings (as the backend
does) just for this: the simulator's only third-party dependency stays
`httpx`, matching the TDD's stated simulator tech stack (section 7).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# Contract section 54's defaults for the variables that matter to the simulator.
DEFAULT_DEVICE_COUNT = 100
DEFAULT_TELEMETRY_INTERVAL_SECONDS = 1.0
DEFAULT_FAULT_RATE = 0.0  # not in section 54's table, but FleetConfig already needs it
DEFAULT_RANDOM_SEED = 42
DEFAULT_BATCH_SIZE = 100  # section 16's "default simulator batch size"

# Where the backend lives is NOT one of section 54's simulation parameters -- it's
# infrastructure-specific, like DATABASE_URL is for the backend, so it gets its own
# environment variable rather than a documented default simulation constant. Local
# development (this step): the backend runs directly on the host at this address.
# Docker Compose will later override this to the Docker DNS name http://backend:8000
# (Roadmap 5.1) -- never "localhost" for container-to-container traffic, per the
# container networking contract.
DEFAULT_BACKEND_URL = "http://localhost:8000"

# Roadmap 5.2: logging. "json" = one JSON object per line (default); "text" = a
# readable line for running the simulator directly in a terminal.
DEFAULT_LOG_LEVEL = "INFO"
DEFAULT_LOG_FORMAT = "json"
_VALID_LOG_FORMATS = ("json", "text")


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got {raw!r}") from exc


@dataclass(frozen=True)
class SimulatorConfig:
    """Every environment-configurable knob 1.10 needs to actually run against a
    live backend. `FleetConfig` (fleet.py) still owns fleet *composition*
    (device count, fault rate, per-battery hardware specs) -- this adds what
    this step introduces on top: where the backend is, and how many events to
    accumulate before sending a batch.
    """

    device_count: int = DEFAULT_DEVICE_COUNT
    telemetry_interval_seconds: float = DEFAULT_TELEMETRY_INTERVAL_SECONDS
    fault_rate: float = DEFAULT_FAULT_RATE
    random_seed: int | None = DEFAULT_RANDOM_SEED
    batch_size: int = DEFAULT_BATCH_SIZE
    backend_url: str = DEFAULT_BACKEND_URL
    log_level: str = DEFAULT_LOG_LEVEL
    log_format: str = DEFAULT_LOG_FORMAT

    @classmethod
    def from_env(cls) -> SimulatorConfig:
        """Build a `SimulatorConfig` from environment variables, falling back to
        the Contract's documented defaults for anything not set. A present but
        unparsable value (e.g. `DEVICE_COUNT=abc`) raises immediately -- Contract
        section 54: "Invalid configuration MUST cause explicit startup failure
        rather than silent substitution."
        """
        log_format = os.environ.get("LOG_FORMAT", DEFAULT_LOG_FORMAT)
        if log_format not in _VALID_LOG_FORMATS:
            raise ValueError(f"LOG_FORMAT must be one of {_VALID_LOG_FORMATS}, got {log_format!r}")
        random_seed_raw = os.environ.get("RANDOM_SEED")
        random_seed = int(random_seed_raw) if random_seed_raw is not None else DEFAULT_RANDOM_SEED
        return cls(
            device_count=_env_int("DEVICE_COUNT", DEFAULT_DEVICE_COUNT),
            telemetry_interval_seconds=_env_float(
                "TELEMETRY_INTERVAL_SECONDS", DEFAULT_TELEMETRY_INTERVAL_SECONDS
            ),
            fault_rate=_env_float("FAULT_RATE", DEFAULT_FAULT_RATE),
            random_seed=random_seed,
            batch_size=_env_int("BATCH_SIZE", DEFAULT_BATCH_SIZE),
            backend_url=os.environ.get("BACKEND_URL", DEFAULT_BACKEND_URL),
            log_level=os.environ.get("LOG_LEVEL", DEFAULT_LOG_LEVEL),
            log_format=log_format,
        )
