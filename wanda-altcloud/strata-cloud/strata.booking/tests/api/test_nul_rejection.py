"""NUL bytes anywhere in a request are rejected with 400 before reaching handlers.

Postgres refuses 0x00 in UTF-8 strings, so without this guard a NUL in a query parameter,
path segment, or JSON body surfaces as a 500 at the database (found by contract fuzzing).
"""

from fastapi.testclient import TestClient

from tests.auth import bearer

ADMIN = bearer("a-1", "admin")


def test_nul_in_query_param_is_400(client: TestClient) -> None:
    """A NUL byte in a query parameter is rejected with 400 naming the problem."""
    resp = client.get("/v1/cancellation-policies", params={"programme_id": "\x00"}, headers=ADMIN)
    assert resp.status_code == 400
    assert "NUL" in resp.json()["detail"]


def test_nul_in_path_is_400(client: TestClient) -> None:
    """A NUL byte in a path segment is rejected with 400."""
    assert client.get("/v1/appointments/%00", headers=ADMIN).status_code == 400


def test_nul_escape_in_json_body_is_400(client: TestClient) -> None:
    """A NUL escape inside a JSON body is rejected with 400."""
    resp = client.post("/v1/appointments/x/cancel", json={"reason": "a\x00b"}, headers=ADMIN)
    assert resp.status_code == 400


def test_every_v1_operation_documents_the_400(client: TestClient) -> None:
    """Every /v1 operation documents a 400, so contract fuzzing holds the schema to it."""
    schema = client.get("/openapi.json").json()
    undocumented = [
        f"{method.upper()} {path}"
        for path, operations in schema["paths"].items()
        if path.startswith("/v1/")
        for method, operation in operations.items()
        if "400" not in operation.get("responses", {})
    ]
    assert undocumented == []


def test_clean_requests_pass_through(client: TestClient) -> None:
    """NUL screening never blocks clean requests."""
    resp = client.get("/v1/cancellation-policies", params={"programme_id": "p-1"}, headers=ADMIN)
    assert resp.status_code == 200
