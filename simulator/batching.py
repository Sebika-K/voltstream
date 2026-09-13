"""Groups individual telemetry events into batches for the HTTP client to send
(Roadmap 1.10; TDD section 7's `generate -> accumulate into batch -> POST
batch -> repeat` pipeline).

Kept separate from both `fleet.py` (which knows nothing about batching or
HTTP) and `client.py` (which knows how to send a batch but not when) -- this
is the middle piece that decides "we have enough events now, flush."
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

FlushCallback = Callable[[list[dict[str, Any]]], Awaitable[None]]


class BatchAccumulator:
    """Collects events and flushes them once `batch_size` is reached.

    `add()` is safe to call concurrently from many coroutines -- exactly how
    `Fleet.run()` uses it: one call per battery per tick, all interleaved on
    the same event loop. Appending to the buffer and deciding whether to
    flush happen with no `await` in between, so two "concurrent" calls (really:
    two coroutines taking turns on one thread) can never both see "buffer is
    full" and double-flush the same events, or race past each other and drop
    one. The buffer being sent is swapped out for a fresh empty list *before*
    the actual flush is awaited, so events arriving while a flush is still in
    flight (e.g. waiting on an HTTP response) land safely in the new buffer
    instead of being lost or duplicated.
    """

    def __init__(self, batch_size: int, flush: FlushCallback) -> None:
        if batch_size < 1:
            raise ValueError(f"batch_size must be >= 1, got {batch_size}")
        self._batch_size = batch_size
        self._flush = flush
        self._buffer: list[dict[str, Any]] = []

    @property
    def pending_count(self) -> int:
        """How many events are currently buffered, waiting for the next flush."""
        return len(self._buffer)

    async def add(self, event: dict[str, Any]) -> None:
        """Buffer one event, flushing immediately if this fills a full batch."""
        self._buffer.append(event)
        if len(self._buffer) >= self._batch_size:
            await self.flush()

    async def flush(self) -> None:
        """Send whatever is currently buffered, even if it's short of a full
        batch. Call this once after the fleet stops, so the last partial
        batch isn't silently dropped -- `add()` alone only flushes on reaching
        `batch_size`.
        """
        if not self._buffer:
            return
        pending, self._buffer = self._buffer, []
        await self._flush(pending)
