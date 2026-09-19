"""Observability bootstrap — structured logging + OpenTelemetry tracing.

Instrumentation is identical everywhere; behaviour is driven by Settings (STRATA_* env vars).
Local default: console logs, tracing off.
"""

from __future__ import annotations

import logging

import structlog
from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SpanExporter,
)
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from sqlalchemy.ext.asyncio import AsyncEngine
from structlog.types import EventDict, WrappedLogger

from app.config import Settings

_provider_configured = False
_engine_instrumented = False


def _add_otel_context(_logger: WrappedLogger, _method: str, event_dict: EventDict) -> EventDict:
    ctx = trace.get_current_span().get_span_context()
    if ctx.is_valid:
        event_dict["trace_id"] = format(ctx.trace_id, "032x")
        event_dict["span_id"] = format(ctx.span_id, "016x")
    return event_dict


def configure_logging(settings: Settings) -> None:
    """Configure structlog: JSON on AWS, human-readable console locally. Safe to call repeatedly."""
    # NEVER render locals in tracebacks (strata.engine.auth's
    # precedent, proven live at the Identity walkthrough): structlog's rich
    # formatter shows frame locals by default, and request handlers hold member
    # data in theirs.
    renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer()
        if settings.log_format == "json"
        else structlog.dev.ConsoleRenderer(
            exception_formatter=structlog.dev.RichTracebackFormatter(show_locals=False)
        )
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            _add_otel_context,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=False,
    )


def _build_exporter(settings: Settings) -> SpanExporter | None:
    kind = settings.otel_traces_exporter
    if kind == "console":
        return ConsoleSpanExporter()
    if kind == "otlp":
        if not settings.otel_exporter_otlp_endpoint:
            raise ValueError(
                "STRATA_OTEL_EXPORTER_OTLP_ENDPOINT is required when "
                "STRATA_OTEL_TRACES_EXPORTER=otlp"
            )
        base = settings.otel_exporter_otlp_endpoint.rstrip("/")
        return OTLPSpanExporter(endpoint=f"{base}/v1/traces")
    return None


def configure_tracing(app: FastAPI, settings: Settings) -> None:
    """Set up the tracer provider (once) and instrument this app. No-op when tracing is off."""
    global _provider_configured
    exporter = _build_exporter(settings)
    if exporter is None:
        return
    if not _provider_configured:
        provider = TracerProvider(
            resource=Resource.create(
                {
                    "service.name": settings.service_name,
                    "deployment.environment": settings.environment,
                }
            ),
            sampler=ParentBased(TraceIdRatioBased(settings.otel_traces_sampler_ratio)),
        )
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        _provider_configured = True
    FastAPIInstrumentor.instrument_app(app)


def instrument_engine(engine: AsyncEngine, settings: Settings) -> None:
    """Instrument SQLAlchemy for SQL query spans. Called from the lifespan (needs the engine)."""
    global _engine_instrumented
    if settings.otel_traces_exporter == "none" or _engine_instrumented:
        return
    SQLAlchemyInstrumentor().instrument(engine=engine.sync_engine)
    _engine_instrumented = True
