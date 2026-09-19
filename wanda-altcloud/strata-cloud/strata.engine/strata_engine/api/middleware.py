"""Correlation-ID middleware — the request-scoped half of observability.

Binds a request id onto the log context and the active trace span, returns it to the caller as
``X-Request-Id``, and emits one structured access log per request. OTel's propagator already
extracts inbound W3C ``traceparent`` / API-Gateway trace context into the span, so this only owns
the human-facing request id and the access line.
"""

from __future__ import annotations

import time
import uuid

import structlog
from opentelemetry import trace
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

REQUEST_ID_HEADER = "X-Request-Id"

_log = structlog.get_logger("strata.access")


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        structlog.contextvars.bind_contextvars(request_id=request_id)

        span = trace.get_current_span()
        if span.get_span_context().is_valid:
            span.set_attribute("request.id", request_id)

        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
            _log.exception(
                "request_failed",
                method=request.method,
                path=request.url.path,
                latency_ms=elapsed_ms,
            )
            structlog.contextvars.clear_contextvars()
            raise

        elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
        response.headers[REQUEST_ID_HEADER] = request_id
        _log.info(
            "request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            latency_ms=elapsed_ms,
        )
        structlog.contextvars.clear_contextvars()
        return response
