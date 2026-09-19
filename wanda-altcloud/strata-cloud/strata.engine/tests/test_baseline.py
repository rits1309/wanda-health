"""Service-baseline conformance gate — CI-enforced.

Fails the build if this shell drifts from the platform baseline: the observability
correlation-ID middleware must be installed and the operational endpoint stays unversioned.
(The reference-data `/v1` assertions moved to ``../strata.terminology`` with the domains —
; a `/v1` versioning assertion returns here with the first new router.)
"""

import pytest
from fastapi.testclient import TestClient

from strata_engine.api.middleware import CorrelationIdMiddleware
from strata_engine.main import create_app


def test_observability_middleware_is_installed() -> None:
    # Observability baseline: the correlation-ID middleware must be wired in.
    """The baseline correlation-ID middleware is installed on the app."""
    app = create_app()
    assert any(m.cls is CorrelationIdMiddleware for m in app.user_middleware), (
        "service baseline violated: CorrelationIdMiddleware is not installed "
    )


def test_health_is_unversioned_and_open(client: TestClient) -> None:
    """The operational /health endpoint stays unversioned and open; /v1/health does not exist."""
    assert client.get("/health").status_code == 200
    assert client.get("/v1/health").status_code == 404


def test_unhandled_exceptions_never_print_request_locals(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An exception NO handler catches must not render frame locals — request
    handlers hold member data in theirs (strata.engine.auth's
    pin, where the leak was proven live at the Identity walkthrough)."""
    app = create_app()
    sentinel = "PII-sentinel-XK552291"

    @app.post("/pws190-boom")
    async def _boom(payload: dict[str, str]) -> None:
        raise RuntimeError(f"simulated unhandled failure ({len(payload)} fields held)")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post("/pws190-boom", json={"member_reading": sentinel})

    assert response.status_code == 500
    captured = capsys.readouterr()
    assert sentinel not in captured.out and sentinel not in captured.err, (
        "the traceback rendered request locals - a PII leak "
    )
