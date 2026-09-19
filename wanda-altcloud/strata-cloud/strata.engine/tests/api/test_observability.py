"""API tests: the correlation-ID middleware.

Behaviour only — every response carries an ``X-Request-Id`` and a supplied id is echoed
(the /v1 tree moved to strata.terminology, so /health is the covered route).
Tracing export is off by default (STRATA_OTEL_TRACES_EXPORTER=none), so these run with no
network and no collector.
"""

from fastapi.testclient import TestClient

HEADER = "X-Request-Id"


def test_response_includes_request_id(client: TestClient) -> None:
    """Every response carries a non-empty X-Request-Id header."""
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.headers.get(HEADER)  # present and non-empty


def test_supplied_request_id_is_echoed(client: TestClient) -> None:
    """A caller-supplied X-Request-Id is echoed back unchanged."""
    resp = client.get("/health", headers={HEADER: "abc-123"})
    assert resp.headers.get(HEADER) == "abc-123"


def test_generated_request_ids_are_unique_per_request(client: TestClient) -> None:
    """Generated request ids differ between requests, never reused."""
    first = client.get("/health").headers[HEADER]
    second = client.get("/health").headers[HEADER]
    assert first and second and first != second


def test_versioned_route_includes_request_id(client: TestClient) -> None:
    """The middleware stamps X-Request-Id on covered routes, not just an accident of /health."""
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.headers.get(HEADER)
