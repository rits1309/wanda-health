"""API tests: procedure search over HTTP — request shape, status codes, payload."""

from fastapi.testclient import TestClient


def test_search_returns_matching_results(client: TestClient) -> None:
    """GET /v1/procedures/search returns matching codes for a title substring."""
    resp = client.get("/v1/procedures/search", params={"q": "heart"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["query"] == "heart"
    assert body["count"] == 4
    assert "02HA0QZ" in {r["code"] for r in body["results"]}


def test_search_by_code_prefix(client: TestClient) -> None:
    """A procedure code prefix search returns the matching code first."""
    resp = client.get("/v1/procedures/search", params={"q": "02HA0Q"})
    assert resp.status_code == 200
    assert resp.json()["results"][0]["code"] == "02HA0QZ"


def test_search_no_match_is_empty_not_error(client: TestClient) -> None:
    """A no-match procedure search returns an empty result set, not an error."""
    resp = client.get("/v1/procedures/search", params={"q": "zzzznotacode"})
    assert resp.status_code == 200
    assert resp.json() == {"query": "zzzznotacode", "count": 0, "results": []}


def test_missing_query_param_is_422(client: TestClient) -> None:
    """A procedure search without the q parameter is rejected with 422."""
    assert client.get("/v1/procedures/search").status_code == 422


def test_empty_query_param_is_422(client: TestClient) -> None:
    """A procedure search with an empty q is rejected with 422."""
    assert client.get("/v1/procedures/search", params={"q": ""}).status_code == 422
