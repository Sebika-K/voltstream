"""Unit tests for BatchAccumulator (Roadmap 1.10) -- no HTTP, no fleet, no
event loop concerns beyond what asyncio.run() gives a single test function.
"""

from __future__ import annotations

import asyncio

import pytest

from batching import BatchAccumulator


def test_invalid_batch_size_raises():
    for bad_size in (0, -1):
        with pytest.raises(ValueError):
            BatchAccumulator(bad_size, flush=lambda events: None)


def test_adding_fewer_than_batch_size_does_not_flush():
    flushes: list[list[dict]] = []

    async def flush(events):
        flushes.append(events)

    async def scenario():
        accumulator = BatchAccumulator(3, flush)
        await accumulator.add({"n": 1})
        await accumulator.add({"n": 2})
        assert accumulator.pending_count == 2
        assert flushes == []

    asyncio.run(scenario())


def test_reaching_batch_size_flushes_exactly_once():
    flushes: list[list[dict]] = []

    async def flush(events):
        flushes.append(events)

    async def scenario():
        accumulator = BatchAccumulator(3, flush)
        await accumulator.add({"n": 1})
        await accumulator.add({"n": 2})
        await accumulator.add({"n": 3})
        assert flushes == [[{"n": 1}, {"n": 2}, {"n": 3}]]
        assert accumulator.pending_count == 0

    asyncio.run(scenario())


def test_the_buffer_resets_after_a_flush_so_the_next_batch_starts_empty():
    flushes: list[list[dict]] = []

    async def flush(events):
        flushes.append(events)

    async def scenario():
        accumulator = BatchAccumulator(2, flush)
        await accumulator.add({"n": 1})
        await accumulator.add({"n": 2})  # flush #1
        await accumulator.add({"n": 3})
        assert accumulator.pending_count == 1
        assert flushes == [[{"n": 1}, {"n": 2}]]

    asyncio.run(scenario())


def test_manual_flush_sends_a_short_partial_batch():
    flushes: list[list[dict]] = []

    async def flush(events):
        flushes.append(events)

    async def scenario():
        accumulator = BatchAccumulator(10, flush)
        await accumulator.add({"n": 1})
        await accumulator.add({"n": 2})
        await accumulator.flush()  # well short of batch_size=10
        assert flushes == [[{"n": 1}, {"n": 2}]]
        assert accumulator.pending_count == 0

    asyncio.run(scenario())


def test_flushing_an_empty_accumulator_does_nothing():
    flushes: list[list[dict]] = []

    async def flush(events):
        flushes.append(events)

    async def scenario():
        accumulator = BatchAccumulator(5, flush)
        await accumulator.flush()
        assert flushes == []

    asyncio.run(scenario())


def test_many_concurrent_adds_produce_no_lost_or_duplicated_events():
    # Simulates what Fleet.run() actually does: many coroutines calling add()
    # "at the same time" (really: interleaved on one event loop). Nothing here
    # awaits inside the flush, but asyncio.gather still interleaves the
    # coroutines' bookkeeping around each add() call.
    flushes: list[list[dict]] = []

    async def flush(events):
        await asyncio.sleep(0)  # exercise a real suspension point during flush
        flushes.append(events)

    async def add_many(accumulator, start, count):
        for i in range(start, start + count):
            await accumulator.add({"n": i})

    async def scenario():
        accumulator = BatchAccumulator(10, flush)
        await asyncio.gather(
            add_many(accumulator, 0, 25),
            add_many(accumulator, 100, 25),
            add_many(accumulator, 200, 25),
        )
        await accumulator.flush()

        all_sent = [event["n"] for batch in flushes for event in batch]
        assert sorted(all_sent) == sorted(
            list(range(0, 25)) + list(range(100, 125)) + list(range(200, 225))
        )
        # No duplicates, and everything ended up somewhere.
        assert len(all_sent) == len(set(all_sent)) == 75

    asyncio.run(scenario())
