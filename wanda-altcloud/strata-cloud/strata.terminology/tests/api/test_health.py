"""API test: health endpoint drives the real app in-process."""

from fastapi.testclient import TestClient


def test_health_ok(client: TestClient) -> None:
    """GET /health returns 200 with the ok status body."""
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
