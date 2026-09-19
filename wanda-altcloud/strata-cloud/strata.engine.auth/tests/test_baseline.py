"""Service-baseline conformance gate — CI-enforced.

Fails the build if this service drifts from the platform baseline: the
observability correlation-ID middleware must be installed, and the auth APIs
must be versioned under ``/v1``. Copied from the strata.engine reference.
"""

from fastapi.testclient import TestClient

from strata_engine_auth.api.middleware import CorrelationIdMiddleware
from strata_engine_auth.main import create_app


def test_observability_middleware_is_installed() -> None:
    """The baseline correlation-ID middleware is installed on the app."""
    app = create_app()
    installed: list[object] = [m.cls for m in app.user_middleware]
    assert CorrelationIdMiddleware in installed, (
        "service baseline violated: CorrelationIdMiddleware is not installed "
    )


def test_auth_apis_are_versioned(client: TestClient) -> None:
    # Versioning baseline: the auth API lives under /v1; unversioned paths 404.
    """The auth API lives under /v1; unversioned paths do not exist."""
    body = {"identifier": "user@wandahealth.com", "password": "Passw0rd!2026"}
    assert client.post("/v1/auth/login", json=body).status_code == 200
    assert client.post("/auth/login", json=body).status_code == 404


def test_health_is_unversioned(client: TestClient) -> None:
    """The operational /health endpoint stays unversioned and reports ok."""
    assert client.get("/health").status_code == 200
    assert client.get("/health").json() == {"status": "ok"}


def test_responses_carry_the_request_id(client: TestClient) -> None:
    """Every response carries the X-Request-Id header."""
    assert "X-Request-Id" in client.get("/health").headers
