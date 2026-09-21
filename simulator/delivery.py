"""A bounded queue between the simulated batteries and the HTTP sender
(Roadmap 5.4, Contract section 50).

Why it exists. Without it, every time a batch fills up, the battery that filled it
sends the batch itself and the other batteries carry on producing. If the backend
is slow or down, batches that are all waiting on retries pile up with nothing
limiting them: the simulator keeps creating work faster than it can be delivered,
and when the backend comes back it is hit by all of it at once (a "retry storm").

How it works. Finished batches go into a queue with a fixed maximum size. A small,
fixed number of sender workers take batches off the queue and send them. When the
queue is full, `submit()` simply waits -- so the battery that tried to hand over a
batch pauses until there is room. That waiting is the "backpressure": the slowness
of the backend is felt all the way back at the producers, and memory use stays
bounded no matter how long the backend is slow or down.

Nothing is dropped silently. A batch that still cannot be delivered after all its
retries is counted (`batches_dropped`) and was already logged by the client as
`batch_dropped`.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from client import BatchDeliveryError

logger = logging.getLogger("simulator")

Batch = list[dict[str, Any]]
SendBatch = Callable[[Batch], Awaitable[Any]]


class DeliveryQueue:
    def __init__(self, send: SendBatch, *, max_batches: int, workers: int) -> None:
        if max_batches < 1:
            raise ValueError(f"max_batches must be >= 1, got {max_batches}")
        if workers < 1:
            raise ValueError(f"workers must be >= 1, got {workers}")
        self._send = send
        self._max_batches = max_batches
        self._worker_count = workers
        self._queue: asyncio.Queue[Batch] = asyncio.Queue(maxsize=max_batches)
        self._workers: list[asyncio.Task[None]] = []
        self._full_logged = False
        # Counters, so lost or delayed data is visible rather than silent.
        self.batches_sent = 0
        self.batches_dropped = 0
        self.blocked_submissions = 0

    @property
    def workers(self) -> list[asyncio.Task[None]]:
        """The sender tasks. They only ever finish if one of them fails."""
        return list(self._workers)

    def start(self) -> None:
        if self._workers:
            raise RuntimeError("DeliveryQueue is already started")
        self._workers = [
            asyncio.create_task(self._work(), name=f"delivery-worker-{n}")
            for n in range(self._worker_count)
        ]

    async def submit(self, batch: Batch) -> None:
        """Hand a finished batch over for sending. Waits while the queue is full."""
        if self._queue.full():
            self.blocked_submissions += 1
            if not self._full_logged:
                # Log once per "episode", not once per waiting producer.
                self._full_logged = True
                logger.warning(
                    "delivery_queue_full",
                    extra={"queue_size": self._queue.qsize(), "max_batches": self._max_batches},
                )
        await self._queue.put(batch)

    async def _work(self) -> None:
        while True:
            batch = await self._queue.get()
            if self._full_logged and self._queue.empty():
                self._full_logged = False
                logger.info(
                    "delivery_queue_recovered",
                    extra={"queue_size": 0, "max_batches": self._max_batches},
                )
            try:
                await self._send(batch)
                self.batches_sent += 1
            except BatchDeliveryError:
                # Retries ran out; the client already logged `batch_dropped`. Count
                # it and move on: one lost batch must not stop the workers.
                self.batches_dropped += 1
            finally:
                self._queue.task_done()
            # Any other error (e.g. the backend refusing our data with a 404) ends
            # this worker on purpose; `drain()` and the caller notice and stop loudly.

    async def drain(self) -> None:
        """Wait until every submitted batch has been sent or counted as dropped.

        Raises the worker's error if a worker died before the queue emptied,
        instead of waiting forever for work nobody is left to do.
        """
        joined = asyncio.ensure_future(self._queue.join())
        try:
            await asyncio.wait({joined, *self._workers}, return_when=asyncio.FIRST_COMPLETED)
            await asyncio.sleep(0)  # let a worker that is failing right now finish failing
            # Check for failed workers even if the queue looks empty: a worker that
            # dies on the very last batch also marks that batch "done", and that must
            # not be mistaken for "delivered".
            for worker in self._workers:
                if worker.done() and not worker.cancelled():
                    worker.result()  # re-raises the worker's error
            if not joined.done():
                raise RuntimeError("delivery workers stopped before the queue was drained")
        finally:
            joined.cancel()

    async def stop(self) -> None:
        """Stop the workers and log the final counts."""
        for worker in self._workers:
            worker.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        logger.info(
            "delivery_stopped",
            extra={
                "batches_sent": self.batches_sent,
                "batches_dropped": self.batches_dropped,
                "blocked_submissions": self.blocked_submissions,
                "batches_left_in_queue": self._queue.qsize(),
            },
        )
