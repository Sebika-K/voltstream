"""Unit tests for SimulatorConfig (Roadmap 1.10, Contract section 54)."""

from __future__ import annotations

import pytest

from config import (
    DEFAULT_BACKEND_URL,
    DEFAULT_BATCH_SIZE,
    DEFAULT_DEVICE_COUNT,
    DEFAULT_FAULT_RATE,
    DEFAULT_RANDOM_SEED,
    DEFAULT_TELEMETRY_INTERVAL_SECONDS,
    SimulatorConfig,
)


def test_defaults_match_the_contract_when_nothing_is_set(monkeypatch):
    for name in (
        "DEVICE_COUNT",
        "TELEMETRY_INTERVAL_SECONDS",
        "FAULT_RATE",
        "RANDOM_SEED",
        "BATCH_SIZE",
        "BACKEND_URL",
    ):
        monkeypatch.delenv(name, raising=False)

    config = SimulatorConfig.from_env()

    assert config.device_count == DEFAULT_DEVICE_COUNT == 100
    assert config.telemetry_interval_seconds == DEFAULT_TELEMETRY_INTERVAL_SECONDS == 1.0
    assert config.fault_rate == DEFAULT_FAULT_RATE == 0.0
    assert config.random_seed == DEFAULT_RANDOM_SEED == 42
    assert config.batch_size == DEFAULT_BATCH_SIZE == 100
    assert config.backend_url == DEFAULT_BACKEND_URL == "http://localhost:8000"


def test_environment_variables_override_every_default(monkeypatch):
    monkeypatch.setenv("DEVICE_COUNT", "250")
    monkeypatch.setenv("TELEMETRY_INTERVAL_SECONDS", "0.5")
    monkeypatch.setenv("FAULT_RATE", "0.1")
    monkeypatch.setenv("RANDOM_SEED", "7")
    monkeypatch.setenv("BATCH_SIZE", "50")
    monkeypatch.setenv("BACKEND_URL", "http://backend:8000")

    config = SimulatorConfig.from_env()

    assert config.device_count == 250
    assert config.telemetry_interval_seconds == 0.5
    assert config.fault_rate == 0.1
    assert config.random_seed == 7
    assert config.batch_size == 50
    assert config.backend_url == "http://backend:8000"


def test_an_unparsable_integer_value_fails_loudly_rather_than_falling_back(monkeypatch):
    monkeypatch.setenv("DEVICE_COUNT", "not-a-number")
    with pytest.raises(ValueError, match="DEVICE_COUNT"):
        SimulatorConfig.from_env()


def test_an_unparsable_float_value_fails_loudly_rather_than_falling_back(monkeypatch):
    monkeypatch.setenv("TELEMETRY_INTERVAL_SECONDS", "soon")
    with pytest.raises(ValueError, match="TELEMETRY_INTERVAL_SECONDS"):
        SimulatorConfig.from_env()
