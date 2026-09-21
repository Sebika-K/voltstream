"""Retry behaviour of BackendClient.send_batch (Roadmap 5.3).

Sleeping is replaced by a recorder and the random source by a stub, so these
tests run instantly and are fully deterministic.
"""

from __future__ import annotations

import asyncio
import json
import logging

import httpx
import pytest

from client import BackendClient, BatchDeliveryError
from retry import RetryPolicy

EVENT = {"timestamp": "2026-01-01T00:00:00Z", "battery_id": "BAT-000001", "event_id": "e-1"}
OK_BODY = {"received": 1, "inserted": 1, "duplicates": 0}


class MaxRng:
    """Stand-in for random.Random: always picks the top of the range."""

    def uniform(self, low: float, high: float) -> float:
        return high


def build(handler, policy: RetryPolicy, sleeps: list[float]) -> BackendClient:
    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://testserver"
    )
    return BackendClient(
        "http://testserver",
        http_client=http_client,
        retry_policy=policy,
        sleep=fake_sleep,
        rng=MaxRng(),
    )


def run(handler, policy: RetryPolicy, sleeps: list[float]):
    async def scenario():
        client = build(handler, policy, sleeps)
        try:
            return await client.send_batch([EVENT])
        finally:
            await client.aclose()

    return asyncio.run(scenario())


def failing_then_ok(failures: int, requests: list[httpx.Request], *, error: str = "status"):
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) <= failures:
            if error == "connect":
                raise httpx.ConnectError("refused", request=request)
            return httpx.Response(503, json={"error": {"code": "DOWN", "message": "x"}})
        return httpx.Response(201, json=OK_BODY)

    return handler


def test_success_on_the_first_try_never_sleeps():
    requests, sleeps = [], []
    result = run(failing_then_ok(0, requests), RetryPolicy(), sleeps)
    assert result == OK_BODY
    assert len(requests) == 1
    assert sleeps == []


@pytest.mark.parametrize("error", ["status", "connect"])
def test_a_temporary_failure_is_retried_until_it_succeeds(error):
    requests, sleeps = [], []
    result = run(failing_then_ok(2, requests, error=error), RetryPolicy(), sleeps)
    assert result == OK_BODY
    assert len(requests) == 3
    assert len(sleeps) == 2


def test_every_attempt_sends_the_same_body_and_request_id():
    requests, sleeps = [], []
    run(failing_then_ok(2, requests), RetryPolicy(), sleeps)
    assert len({r.headers["x-request-id"] for r in requests}) == 1
    assert len({r.content for r in requests}) == 1
    assert json.loads(requests[0].content)["events"][0]["event_id"] == "e-1"


def test_waits_grow_exponentially_and_are_capped():
    requests, sleeps = [], []
    policy = RetryPolicy(max_attempts=8, base_delay_seconds=0.5, max_delay_seconds=4.0)
    with pytest.raises(BatchDeliveryError):
        run(failing_then_ok(99, requests), policy, sleeps)
    # MaxRng returns each ceiling exactly: 0.5, 1, 2, 4, 4, 4, 4
    assert sleeps == [0.5, 1.0, 2.0, 4.0, 4.0, 4.0, 4.0]


def test_exhausted_retries_raise_batch_delivery_error(caplog):
    caplog.set_level(logging.INFO)
    requests, sleeps = [], []
    policy = RetryPolicy(max_attempts=4)
    with pytest.raises(BatchDeliveryError) as info:
        run(failing_then_ok(99, requests), policy, sleeps)

    assert len(requests) == 4
    assert len(sleeps) == 3  # no wait after the final attempt
    assert info.value.attempts == 4
    assert info.value.event_count == 1
    assert isinstance(info.value.__cause__, httpx.HTTPStatusError)

    failed = [r for r in caplog.records if r.getMessage() == "batch_failed"]
    assert [r.attempt for r in failed] == [1, 2, 3, 4]
    assert [r.will_retry for r in failed] == [True, True, True, False]
    assert all(r.levelno == logging.WARNING for r in failed[:3])
    (dropped,) = [r for r in caplog.records if r.getMessage() == "batch_dropped"]
    assert dropped.levelno == logging.ERROR
    assert dropped.attempts == 4


def test_a_recovered_batch_logs_the_attempt_it_succeeded_on(caplog):
    caplog.set_level(logging.INFO)
    requests, sleeps = [], []
    run(failing_then_ok(2, requests), RetryPolicy(), sleeps)
    (sent,) = [r for r in caplog.records if r.getMessage() == "batch_sent"]
    assert sent.attempt == 3
    assert not [r for r in caplog.records if r.getMessage() == "batch_dropped"]


def test_a_non_retryable_error_is_raised_immediately(caplog):
    caplog.set_level(logging.INFO)
    requests, sleeps = [], []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(404, json={"error": {"code": "NOT_FOUND", "message": "x"}})

    with pytest.raises(httpx.HTTPStatusError):
        run(handler, RetryPolicy(), sleeps)

    assert len(requests) == 1
    assert sleeps == []
    assert not [r for r in caplog.records if r.getMessage() == "batch_dropped"]
    (failed,) = [r for r in caplog.records if r.getMessage() == "batch_failed"]
    assert failed.will_retry is False


def test_one_attempt_means_no_retry():
    requests, sleeps = [], []
    with pytest.raises(BatchDeliveryError):
        run(failing_then_ok(99, requests), RetryPolicy(max_attempts=1), sleeps)
    assert len(requests) == 1
    assert sleeps == []
