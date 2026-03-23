"""Correlation ID middleware for per-request tracing."""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

from starlette.middleware.base import BaseHTTPMiddleware

from claw_plaid_ledger.logging_utils import (
    reset_correlation_id,
    set_correlation_id,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from fastapi import Request
    from starlette.responses import Response

logger = logging.getLogger(__name__)


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """
    Attach a unique request_id to every HTTP request.

    - Generates ``request_id = "req-" + uuid4().hex[:8]`` per request.
    - Stores it in a ``ContextVar`` so all code in the request call stack
      picks it up via the ``CorrelationIdFilter`` without explicit threading.
    - Emits INFO logs at request start and end.
    - Adds ``X-Request-Id`` to the response headers.
    """

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """Generate request_id, set context var, log, add header."""
        request_id = "req-" + uuid.uuid4().hex[:8]
        token = set_correlation_id(request_id)
        logger.info(
            "request_start method=%s path=%s request_id=%s",
            request.method,
            request.url.path,
            request_id,
        )
        try:
            response = await call_next(request)
        finally:
            reset_correlation_id(token)
        logger.info(
            "request_end method=%s path=%s status=%d request_id=%s",
            request.method,
            request.url.path,
            response.status_code,
            request_id,
        )
        response.headers["X-Request-Id"] = request_id
        return response
