"""Tests for the retry rules (Roadmap 5.3, Contract section 49).

Pure logic: nothing here sends a request or sleeps. `is_retryable` is checked
against real httpx exception objects; `delay_for` is checked with stand-in random
generators so the exact numbers are known.
"""

from __future__ import annotations

import random

import httpx
import pytest

from config import SimulatorConfig
from retry import RetryPolicy, is_retryable


def status_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "http://testserver/api/v1/telemetry/batch")
    response = httpx.Response(status, request=request)
    return httpx.HTTPStatusError(f"status {status}", request=request, response=response)


class AlwaysMax:
    """Stand-in for random.Random: always picks the top of the range."""

    def uniform(self, low: float, high: float) -> float:
        return high


class AlwaysMin:
    def uniform(self, low: float, high: float) -> float:
        return low


# --- RetryPolicy ---------------------------------------------------------------


def test_defaults_are_bounded_and_sensible():
    policy = RetryPolicy()

    assert policy.max_attempts == 10
    assert policy.base_delay_seconds == 0.5
    assert policy.max_delay_seconds == 30.0


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_attempts": 0},
        {"base_delay_seconds": 0},
        {"base_delay_seconds": -1.0},
        {"base_delay_seconds": 5.0, "max_delay_seconds": 1.0},
    ],
)
def test_invalid_settings_fail_loudly(kwargs):
    with pytest.raises(ValueError):
        RetryPolicy(**kwargs)


def test_one_attempt_means_no_retries_and_is_allowed():
    assert RetryPolicy(max_attempts=1).max_attempts == 1


def test_delay_ceiling_doubles_then_stops_at_the_maximum():
    policy = RetryPolicy(base_delay_seconds=0.5, max_delay_seconds=30.0)

    ceilings = [policy.delay_ceiling(attempt) for attempt in range(1, 9)]

    assert ceilings == [0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 30.0]


def test_delay_can_reach_the_ceiling_and_can_be_zero():
    policy = RetryPolicy()

    assert policy.delay_for(4, AlwaysMax()) == 4.0
    assert policy.delay_for(4, AlwaysMin()) == 0.0


def test_jitter_really_varies_but_never_exceeds_the_ceiling():
    policy = RetryPolicy()
    rng = random.Random(123)

    delays = [policy.delay_for(5, rng) for _ in range(50)]

    assert len(set(delays)) > 40  # not all the same wait
    assert all(0 <= delay <= policy.delay_ceiling(5) for delay in delays)


def test_same_seed_gives_the_same_delays():
    policy = RetryPolicy()

    first = [policy.delay_for(n, random.Random(7)) for n in range(1, 6)]
    second = [policy.delay_for(n, random.Random(7)) for n in range(1, 6)]

    assert first == second


# --- is_retryable --------------------------------------------------------------


@pytest.mark.parametrize(
    "exc",
    [
        httpx.ConnectError("connection refused"),
        httpx.ReadTimeout("read timed out"),
        httpx.ConnectTimeout("connect timed out"),
        httpx.RemoteProtocolError("server disconnected"),
    ],
)
def test_network_problems_are_retryable(exc):
    assert is_retryable(exc) is True


@pytest.mark.parametrize("status", [500, 502, 503, 504, 408, 425, 429])
def test_server_trouble_statuses_are_retryable(status):
    assert is_retryable(status_error(status)) is True


@pytest.mark.parametrize("status", [400, 404, 409, 413, 422])
def test_client_error_statuses_are_not_retryable(status):
    assert is_retryable(status_error(status)) is False


@pytest.mark.parametrize("exc", [ValueError("bug"), KeyError("x"), RuntimeError("boom")])
def test_ordinary_program_errors_are_not_retryable(exc):
    assert is_retryable(exc) is False


# --- config --------------------------------------------------------------------


def test_config_defaults_match_the_policy_defaults(monkeypatch):
    for name in ("RETRY_MAX_ATTEMPTS", "RETRY_BASE_DELAY_SECONDS", "RETRY_MAX_DELAY_SECONDS"):
        monkeypatch.delenv(name, raising=False)

    assert SimulatorConfig.from_env().retry_policy == RetryPolicy()


def test_config_reads_retry_settings_from_the_environment(monkeypatch):
    monkeypatch.setenv("RETRY_MAX_ATTEMPTS", "3")
    monkeypatch.setenv("RETRY_BASE_DELAY_SECONDS", "0.1")
    monkeypatch.setenv("RETRY_MAX_DELAY_SECONDS", "2")

    policy = SimulatorConfig.from_env().retry_policy

    assert policy == RetryPolicy(max_attempts=3, base_delay_seconds=0.1, max_delay_seconds=2.0)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("RETRY_MAX_ATTEMPTS", "0"),
        ("RETRY_MAX_ATTEMPTS", "many"),
        ("RETRY_BASE_DELAY_SECONDS", "-1"),
        ("RETRY_MAX_DELAY_SECONDS", "0.01"),  # smaller than the default base delay of 0.5
    ],
)
def test_config_rejects_invalid_retry_settings_at_startup(monkeypatch, name, value):
    monkeypatch.setenv(name, value)

    with pytest.raises(ValueError):
        SimulatorConfig.from_env()
