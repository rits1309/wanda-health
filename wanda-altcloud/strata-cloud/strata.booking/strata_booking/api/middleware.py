"""Correlation-ID middleware — the request-scoped half of observability."""

from __future__ import annotations

import time
import uuid
from urllib.parse import unquote

import structlog
from opentelemetry import trace
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

REQUEST_ID_HEADER = "X-Request-Id"

_log = structlog.get_logger("access")


class NulByteRejectionMiddleware:
    """Reject requests carrying NUL bytes with a 400 before they reach any handler.

    Postgres refuses ``0x00`` inside UTF-8 strings, so a NUL smuggled through a path segment,
    query parameter, or JSON body would surface as a 500 at the database instead of a client
    error. No legitimate request to this API contains one.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        query = scope.get("query_string", b"").decode("latin-1")
        if "\x00" in scope.get("path", "") or "\x00" in unquote(query):
            await self._reject(send)
            return
        chunks: list[bytes] = []
        while True:
            message = await receive()
            if message["type"] != "http.request":
                await self.app(scope, _replay(message, receive), send)
                return
            chunks.append(message.get("body", b""))
            if not message.get("more_body", False):
                break
        body = b"".join(chunks)
        # Both the raw byte and the JSON escape that would decode to one.
        if b"\x00" in body or b"\\u0000" in body:
            await self._reject(send)
            return
        replay = _replay({"type": "http.request", "body": body, "more_body": False}, receive)
        await self.app(scope, replay, send)

    @staticmethod
    async def _reject(send: Send) -> None:
        response = JSONResponse({"detail": "Request must not contain NUL bytes"}, status_code=400)
        await response({"type": "http"}, _noop_receive, send)


def _replay(first: Message, receive: Receive) -> Receive:
    """A receive callable that yields the buffered first message, then defers to the original."""
    delivered = False

    async def replay() -> Message:
        nonlocal delivered
        if not delivered:
            delivered = True
            return first
        return await receive()

    return replay


async def _noop_receive() -> Message:
    return {"type": "http.disconnect"}


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
