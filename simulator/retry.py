"""Retry rules for the simulator (Roadmap 5.3, Contract section 49).

This module only *decides* things -- which failures are worth retrying, and how
long to wait before the next attempt. It sends nothing and sleeps for nothing,
which keeps it easy to test exactly. (`client.py` is what actually uses it.)

Why retry at all: a "temporary" failure (the backend is restarting, the network
hiccuped, the database was briefly busy) usually fixes itself within seconds. If
one failed request ended the simulation, every brief hiccup would be a crash.

Why retrying is *safe* here: every telemetry event carries a unique `event_id`
generated before the first attempt, and the backend ignores an `event_id` it has
already stored (Roadmap 1.8). Sending the same batch twice can never create
duplicate rows -- so if a request actually reached the database but the reply got
lost, the retry is harmless.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

import httpx

# HTTP status codes that mean "try again later" rather than "your request is wrong":
# 408 Request Timeout, 425 Too Early, 429 Too Many Requests, and every 5xx
# (the server itself failed). A 4xx like 404 or 422 would fail identically forever.
_RETRYABLE_STATUS_CODES = frozenset({408, 425, 429})


@dataclass(frozen=True)
class RetryPolicy:
    """How persistent to be. Bounded and configurable, per Contract section 49
    ("The retry configuration MUST be bounded/configurable").

    `max_attempts` counts the first try too: 1 means "never retry".
    """

    max_attempts: int = 10
    base_delay_seconds: float = 0.5
    max_delay_seconds: float = 30.0

    def __post_init__(self) -> None:
        # Contract section 54: invalid configuration fails loudly, not silently.
        if self.max_attempts < 1:
            raise ValueError(f"max_attempts must be >= 1, got {self.max_attempts}")
        if self.base_delay_seconds <= 0:
            raise ValueError(
                f"base_delay_seconds must be > 0, got {self.base_delay_seconds}"
            )
        if self.max_delay_seconds < self.base_delay_seconds:
            raise ValueError(
                "max_delay_seconds must be >= base_delay_seconds, got "
                f"{self.max_delay_seconds} < {self.base_delay_seconds}"
            )

    def delay_ceiling(self, attempt: int) -> float:
        """The longest we could wait after failed attempt number `attempt` (1-based).

        Exponential backoff: the ceiling doubles with every failure --
        0.5s, 1s, 2s, 4s, 8s, ... -- until it reaches `max_delay_seconds`.
        Waiting longer each time means a struggling backend isn't hammered by
        constant retries while it tries to recover.
        """
        return min(self.max_delay_seconds, self.base_delay_seconds * 2 ** (attempt - 1))

    def delay_for(self, attempt: int, rng: random.Random) -> float:
        """How long to actually wait after failed attempt number `attempt`.

        "Full jitter": a random time between zero and the ceiling, not the
        ceiling itself. Why the randomness matters: if the backend goes down and
        comes back, every client that failed at the same moment would otherwise
        retry at exactly the same moments too, hitting the recovering backend in
        synchronized waves. Random delays spread them out.
        """
        return rng.uniform(0, self.delay_ceiling(attempt))


def is_retryable(exc: BaseException) -> bool:
    """Is this failure the kind that might succeed if tried again?

    Retry: the backend couldn't be reached or didn't answer in time (connection
    refused, timeouts, dropped connections), or it answered with a "server
    trouble" status (5xx, plus 408/425/429).

    Do NOT retry: a 4xx such as 404 (unknown battery), 409, 413 or 422. The
    backend understood the request and refused it; sending the identical request
    again gets the identical refusal, so retrying only delays noticing a real
    problem.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return status in _RETRYABLE_STATUS_CODES or status >= 500
    # TransportError is httpx's parent class for "the request never got a proper
    # answer": ConnectError, ReadTimeout, WriteError, RemoteProtocolError, ...
    return isinstance(exc, httpx.TransportError)
