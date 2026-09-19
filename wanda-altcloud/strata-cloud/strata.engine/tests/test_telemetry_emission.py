"""Observability emission tests — prove the service actually *produces* telemetry.

Two layers, complementing the wiring checks in ``tests/api/test_observability.py``:
- fast (no Docker): an in-memory span exporter + captured structlog output, asserting a request
  emits a span and a structured access log carrying the request id;
- integration (``@pytest.mark.integration``, needs Docker): spins a real Jaeger via testcontainers,
  exports OTLP to it, and asserts the span is retrievable from Jaeger's query API — the genuine
  "traces reach the collector" guarantee (the same shape as X-Ray on AWS).
"""

import io
import json
import time

import httpx
import pytest
import structlog
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import strata_engine.core.observability as observability
from strata_engine.core.config import settings
from strata_engine.main import create_app


def test_requests_emit_spans() -> None:
    """A handled request emits a span that references the route."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    app = create_app()
    FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)
    try:
        TestClient(app).get("/health")
    finally:
        FastAPIInstrumentor.uninstrument_app(app)
    spans = exporter.get_finished_spans()
    assert spans, "no spans emitted — the service is not producing traces"
    detail = " ".join((s.name or "") + str(dict(s.attributes or {})) for s in spans)
    assert "health" in detail, "the request span does not reference the route"


def test_requests_emit_structured_logs_with_request_id() -> None:
    """A handled request emits a structured access log carrying the request_id."""
    app = create_app()
    buf = io.StringIO()
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.PrintLoggerFactory(file=buf),
        cache_logger_on_first_use=False,
    )
    TestClient(app).get("/health")
    logs = [json.loads(line) for line in buf.getvalue().splitlines() if line.strip()]
    access = [e for e in logs if e.get("event") == "request"]
    assert access, "no structured access log emitted — logging is not producing capturable info"
    assert access[0].get("request_id"), "the access log is missing the request_id"


@pytest.fixture(scope="module")
def jaeger():
    """A real Jaeger (all-in-one) via testcontainers. Skips when Docker is unavailable."""
    from testcontainers.core.container import DockerContainer
    from testcontainers.core.docker_client import DockerClient

    try:
        DockerClient().client.ping()
    except Exception:
        pytest.skip("Docker not available for the Jaeger integration test")

    container = (
        DockerContainer("jaegertracing/all-in-one:1.60")
        .with_env("COLLECTOR_OTLP_ENABLED", "true")
        .with_exposed_ports(4318, 16686)
    )
    container.start()
    try:
        host = container.get_container_host_ip()
        otlp_port = int(container.get_exposed_port(4318))
        query_port = int(container.get_exposed_port(16686))
        services_url = f"http://{host}:{query_port}/api/services"
        for _ in range(60):
            try:
                if httpx.get(services_url, timeout=2).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        yield host, otlp_port, query_port
    finally:
        container.stop()


@pytest.mark.integration
def test_traces_reach_jaeger(jaeger, monkeypatch: pytest.MonkeyPatch) -> None:
    """Spans exported over OTLP are retrievable from a real Jaeger query API end to end."""
    host, otlp_port, query_port = jaeger
    service = "strata-itest"
    monkeypatch.setattr(settings, "service_name", service)
    monkeypatch.setattr(settings, "otel_traces_exporter", "otlp")
    monkeypatch.setattr(settings, "otel_exporter_otlp_endpoint", f"http://{host}:{otlp_port}")
    monkeypatch.setattr(observability, "_provider_configured", False)

    app = create_app()  # configure_tracing wires the OTLP exporter to the Jaeger container
    TestClient(app).get("/health")
    trace.get_tracer_provider().force_flush()  # type: ignore[attr-defined]

    traces_url = f"http://{host}:{query_port}/api/traces"
    for _ in range(30):
        resp = httpx.get(traces_url, params={"service": service}, timeout=5)
        if resp.status_code == 200 and resp.json().get("data"):
            break
        time.sleep(1)
    else:
        pytest.fail("trace never reached Jaeger — OTLP export is not working end to end")

    spans = [s for t in resp.json()["data"] for s in t["spans"]]
    assert any("health" in s["operationName"] for s in spans), "request span not found"
