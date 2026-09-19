"""Service-baseline conformance gate — CI-enforced.

Fails the build if the service drifts from the platform baseline: the observability
correlation-ID middleware must be installed, reference-data APIs must be versioned under
``/v1``, and every ``/v1`` route must sit behind the verification seam (any authenticated
user) while ``/health`` stays open and unversioned.
"""

from fastapi.testclient import TestClient

from strata_terminology.api.middleware import CorrelationIdMiddleware
from strata_terminology.main import create_app


def test_observability_middleware_is_installed() -> None:
    """The baseline correlation-ID middleware is installed on the app."""
    app = create_app()
    assert any(m.cls is CorrelationIdMiddleware for m in app.user_middleware), (
        "service baseline violated: CorrelationIdMiddleware is not installed "
    )


def test_reference_data_apis_are_versioned(client: TestClient) -> None:
    # Versioning baseline: APIs live under /v1; the unversioned path 404s.
    """Reference-data APIs live under /v1; unversioned paths do not exist."""
    assert client.get("/v1/medications/search", params={"q": "zepbound"}).status_code == 200
    assert client.get("/medications/search", params={"q": "zepbound"}).status_code == 404


def test_v1_apis_require_authentication(anon_client: TestClient) -> None:
    # Auth baseline: /v1 is behind the seam; /health stays open.
    """/v1 sits behind the auth seam while /health stays open."""
    assert anon_client.get("/v1/medications/search", params={"q": "zepbound"}).status_code == 401
    assert anon_client.get("/health").status_code == 200
