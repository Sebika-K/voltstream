"""The bounded delivery queue (Roadmap 5.4, Contract section 50).

No network: the "send" function is a fake we control, so each test can hold a send
open, make it fail, or count how many run at once.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from client import BatchDeliveryError
from delivery import DeliveryQueue


def batch(n: int) -> list[dict]:
    return [{"n": n}]


def records(caplog, message: str) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.getMessage() == message]


def test_invalid_settings_fail_loudly():
    async def send(b):  # pragma: no cover - never called
        pass

    with pytest.raises(ValueError):
        DeliveryQueue(send, max_batches=0, workers=1)
    with pytest.raises(ValueError):
        DeliveryQueue(send, max_batches=1, workers=0)


def test_every_submitted_batch_is_sent_and_drain_waits_for_all():
    sent: list[int] = []

    async def send(b):
        await asyncio.sleep(0.001)
        sent.append(b[0]["n"])

    async def scenario():
        queue = DeliveryQueue(send, max_batches=3, workers=2)
        queue.start()
        for n in range(10):
            await queue.submit(batch(n))
        await queue.drain()
        await queue.stop()
        return queue

    queue = asyncio.run(scenario())
    assert sorted(sent) == list(range(10))
    assert queue.batches_sent == 10
    assert queue.batches_dropped == 0


def test_a_full_queue_makes_the_producer_wait_until_there_is_room():
    async def scenario():
        gate = asyncio.Event()
        sent: list[int] = []

        async def send(b):
            await gate.wait()
            sent.append(b[0]["n"])

        queue = DeliveryQueue(send, max_batches=2, workers=1)
        queue.start()
        await queue.submit(batch(0))
        await asyncio.sleep(0.01)  # the worker picks up batch 0 and is stuck sending it
        await queue.submit(batch(1))
        await queue.submit(batch(2))  # the queue (size 2) is now full

        blocked = asyncio.ensure_future(queue.submit(batch(3)))
        await asyncio.sleep(0.05)
        still_waiting = not blocked.done()

        gate.set()  # the backend "recovers"
        await asyncio.wait_for(blocked, timeout=2)
        await queue.drain()
        await queue.stop()
        return still_waiting, sent, queue

    still_waiting, sent, queue = asyncio.run(scenario())
    assert still_waiting, "the 4th batch should have had to wait for room"
    assert sorted(sent) == [0, 1, 2, 3]  # waiting lost nothing
    assert queue.blocked_submissions == 1


def test_never_more_sends_at_once_than_there_are_workers():
    running = 0
    peak = 0

    async def send(b):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.01)
        running -= 1

    async def scenario():
        queue = DeliveryQueue(send, max_batches=20, workers=3)
        queue.start()
        for n in range(12):
            await queue.submit(batch(n))
        await queue.drain()
        await queue.stop()

    asyncio.run(scenario())
    assert 2 <= peak <= 3


def test_a_batch_that_could_not_be_delivered_is_counted_and_work_continues():
    sent: list[int] = []

    async def send(b):
        if b[0]["n"] == 0:
            raise BatchDeliveryError(event_count=1, attempts=10, last_error=RuntimeError("down"))
        sent.append(b[0]["n"])

    async def scenario():
        queue = DeliveryQueue(send, max_batches=5, workers=1)
        queue.start()
        for n in range(3):
            await queue.submit(batch(n))
        await queue.drain()
        await queue.stop()
        return queue

    queue = asyncio.run(scenario())
    assert sent == [1, 2]
    assert queue.batches_dropped == 1
    assert queue.batches_sent == 2


def test_an_unexpected_error_is_raised_by_drain_instead_of_hanging():
    async def send(b):
        raise RuntimeError("backend refused this data")

    async def scenario():
        queue = DeliveryQueue(send, max_batches=5, workers=1)
        queue.start()
        await queue.submit(batch(0))
        try:
            await asyncio.wait_for(queue.drain(), timeout=2)
        finally:
            await queue.stop()

    with pytest.raises(RuntimeError, match="refused"):
        asyncio.run(scenario())


def test_queue_full_is_logged_once_per_episode_and_the_final_counts_are_logged(caplog):
    caplog.set_level(logging.INFO)

    async def scenario():
        gate = asyncio.Event()

        async def send(b):
            await gate.wait()

        queue = DeliveryQueue(send, max_batches=1, workers=1)
        queue.start()
        await queue.submit(batch(0))
        await asyncio.sleep(0.01)
        await queue.submit(batch(1))  # fills the queue
        waiting = [asyncio.ensure_future(queue.submit(batch(n))) for n in (2, 3, 4)]
        await asyncio.sleep(0.02)
        gate.set()
        await asyncio.gather(*waiting)
        await queue.drain()
        await queue.stop()

    asyncio.run(scenario())

    assert len(records(caplog, "delivery_queue_full")) == 1  # three waited, one log line
    assert records(caplog, "delivery_queue_recovered")
    (stopped,) = records(caplog, "delivery_stopped")
    assert stopped.batches_sent == 5
    assert stopped.batches_dropped == 0
    assert stopped.blocked_submissions == 3
