"""The shared API error envelope (TDD section 12 / Contract's error contract).

Every product endpoint that needs to report a business-logic error (not found,
conflict, etc.) should raise `APIError` rather than FastAPI's own
`HTTPException` -- `HTTPException`'s default body is `{"detail": ...}`, but the
Contract specifies a consistent shape instead:

    {"error": {"code": "...", "message": "..."}}

Registering this once, here, in `app.main`, means every future endpoint (1.7's
ingestion, later battery lookups, etc.) gets the same error shape for free
just by raising `APIError` -- nobody has to remember to hand-build the
envelope themselves.

Pydantic's own validation errors (a malformed request body) are unaffected --
those still return FastAPI's default 422 shape, which the Contract doesn't
override.
"""

from __future__ import annotations

import logging

from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from app.core.metrics import ERRORS

logger = logging.getLogger(__name__)


class APIError(Exception):
    """A business-logic error that should reach the client as the Contract's
    `{"error": {"code", "message"}}` envelope."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        super().__init__(message)


async def api_error_handler(request: Request, exc: APIError) -> JSONResponse:
    # Roadmap 5.2: the one place every business-rule rejection (unknown battery,
    # conflicting registration, oversized batch, ...) passes through, so logging it
    # here covers every endpoint at once. WARNING, not ERROR: the caller did
    # something we refuse, the server itself is working as designed. The request ID
    # is added to the line automatically; the message names the battery involved.
    logger.warning(
        "request_rejected",
        extra={
            "method": request.method,
            "path": request.url.path,
            "status_code": exc.status_code,
            "error_code": exc.code,
            "error": exc.message,
        },
    )
    ERRORS.labels(code=exc.code).inc()
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": exc.message}},
    )


# The ways the database can be "temporarily not there" from a request's point of view
# (Contract section 51: this must be a controlled 503, not an unhandled 500):
#   * PoolTimeoutError -- every pooled connection was busy and none freed up in time
#     (overload; this is what filled the logs during the Roadmap 5.3 failure test);
#   * OperationalError / InterfaceError -- the connection broke or could not be used;
# Python's plain ConnectionError is deliberately left out: it is also what a browser
# closing a live stream looks like, and that must not be reported as a database outage.
# A genuine bug (bad SQL, a constraint violation, a deadlock) is deliberately NOT in
# this list: that is a real server error and should still be a 500.
DATABASE_UNAVAILABLE_ERRORS: tuple[type[Exception], ...] = (
    PoolTimeoutError,
    OperationalError,
    InterfaceError,
)


async def database_unavailable_handler(request: Request, exc: Exception) -> JSONResponse:
    """Turn "the database is overloaded or unreachable" into a clean 503.

    503 tells the caller "the server is fine, but temporarily can't serve this --
    try again later", which is exactly what a retrying client like the simulator
    should hear. The response never includes the database's own error text (it can
    contain hostnames and SQL); that goes to the log only.
    """
    logger.error(
        "database_unavailable_for_request",
        extra={
            "method": request.method,
            "path": request.url.path,
            "status_code": 503,
            "error_code": "DATABASE_UNAVAILABLE",
            "error": f"{type(exc).__name__}",
        },
    )
    ERRORS.labels(code="DATABASE_UNAVAILABLE").inc()
    return JSONResponse(
        status_code=503,
        content={
            "error": {
                "code": "DATABASE_UNAVAILABLE",
                "message": "The database is temporarily unavailable. Please try again shortly.",
            }
        },
    )
