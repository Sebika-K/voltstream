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
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": exc.message}},
    )
