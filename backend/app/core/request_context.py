"""Request ID + per-request logging middleware (Roadmap 5.2, Contract section 53).

For every HTTP request this does four things:

1. Decides the request's ID -- reusing a well-formed `X-Request-ID` the caller
   sent, otherwise generating a fresh one.
2. Stores it in `request_id_var` so every log line written while handling the
   request carries it automatically (see logging_config.py).
3. Returns it to the caller in the `X-Request-ID` response header, so someone
   who sees an error can quote the ID and you can find exactly that request in
   the logs.
4. Writes one `request_completed` log line (method, path, status, duration) --
   or `request_failed` with the error if the request crashed.

It is written as a plain "ASGI middleware" rather than using Starlette's
convenient `BaseHTTPMiddleware` on purpose: that helper is known to misbehave
with long-lived streaming responses, and this app has one (the SSE stream at
`/api/v1/stream`).
"""

from __future__ import annotations

import logging
import re
import time
import uuid

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.logging_config import request_id_var

logger = logging.getLogger("app.request")

# An incoming ID is only trusted if it looks like a plain identifier: letters,
# digits, dot, underscore, dash, at most 64 characters. Anything else (spaces,
# angle brackets, a 10,000-character string, ...) is thrown away and replaced,
# because this value gets echoed into a response header and written into logs.
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

# The Docker healthcheck calls /ready every few seconds. Logging each of those
# at INFO would bury the useful lines, so they are logged at DEBUG instead.
_QUIET_PATHS = frozenset({"/health", "/ready"})


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = None
        for name, value in scope["headers"]:
            if name == b"x-request-id":
                incoming = value.decode("latin-1")
                break
        request_id = (
            incoming if incoming and _VALID_REQUEST_ID.match(incoming) else uuid.uuid4().hex
        )

        token = request_id_var.set(request_id)
        started = time.perf_counter()
        status_code = 500  # what we report if the app crashes before responding

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode("latin-1")))
                message = {**message, "headers": headers}
            await send(message)

        path = scope["path"]
        try:
            await self.app(scope, receive, send_with_request_id)
        except Exception:
            logger.exception(
                "request_failed",
                extra={
                    "request_id": request_id,
                    "method": scope["method"],
                    "path": path,
                    "status_code": 500,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                },
            )
            raise
        else:
            logger.log(
                logging.DEBUG if path in _QUIET_PATHS else logging.INFO,
                "request_completed",
                extra={
                    "request_id": request_id,
                    "method": scope["method"],
                    "path": path,
                    "status_code": status_code,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                },
            )
        finally:
            request_id_var.reset(token)
