"""API tests: diagnosis search over HTTP — request shape, status codes, payload."""

from fastapi.testclient import TestClient


def test_search_returns_matching_results(client: TestClient) -> None:
    """GET /v1/diagnoses/search returns matching codes for a title substring."""
    resp = client.get("/v1/diagnoses/search", params={"q": "hyperten"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["query"] == "hyperten"
    assert "G932" in {r["code"] for r in body["results"]}


def test_search_by_code_with_decimal(client: TestClient) -> None:
    # A user-typed decimal is stripped: "G93.2" finds G932.
    """A user-typed decimal code (G93.2) still finds the stored undotted code."""
    resp = client.get("/v1/diagnoses/search", params={"q": "G93.2"})
    assert resp.status_code == 200
    assert resp.json()["results"][0]["code"] == "G932"


def test_search_no_match_is_empty_not_error(client: TestClient) -> None:
    """A no-match diagnosis search returns an empty result set, not an error."""
    resp = client.get("/v1/diagnoses/search", params={"q": "zzzznotacode"})
    assert resp.status_code == 200
    assert resp.json() == {"query": "zzzznotacode", "count": 0, "results": []}


def test_missing_query_param_is_422(client: TestClient) -> None:
    """A diagnosis search without the q parameter is rejected with 422."""
    assert client.get("/v1/diagnoses/search").status_code == 422


def test_empty_query_param_is_422(client: TestClient) -> None:
    """A diagnosis search with an empty q is rejected with 422."""
    assert client.get("/v1/diagnoses/search", params={"q": ""}).status_code == 422
