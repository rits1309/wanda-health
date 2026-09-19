"""API tests: medication search over HTTP — request shape, status codes, payload."""

from fastapi.testclient import TestClient


def test_search_returns_matching_results(client: TestClient) -> None:
    """GET /v1/medications/search resolves a brand to its generic with routes + ingredients."""
    resp = client.get("/v1/medications/search", params={"q": "zepbound"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["query"] == "zepbound"
    assert body["count"] == 1
    assert body["results"][0]["generic_name"] == "tirzepatide"
    #: route + per-ingredient detail ride the wire for Summit's picker.
    assert body["results"][0]["routes"] == ["SUBCUTANEOUS"]
    assert body["results"][0]["ingredients"] == [{"name": "TIRZEPATIDE", "strength": "25 mg/mL"}]


def test_search_by_generic_returns_multiple(client: TestClient) -> None:
    """A generic-name search returns every brand carrying that ingredient."""
    resp = client.get("/v1/medications/search", params={"q": "tirzepatide"})
    assert resp.status_code == 200
    assert resp.json()["count"] == 2  # Zepbound + Mounjaro


def test_search_no_match_is_empty_not_error(client: TestClient) -> None:
    """A no-match medication search returns an empty result set, not an error."""
    resp = client.get("/v1/medications/search", params={"q": "zzzznotadrug"})
    assert resp.status_code == 200
    assert resp.json() == {"query": "zzzznotadrug", "count": 0, "results": []}


def test_missing_query_param_is_422(client: TestClient) -> None:
    """A medication search without the q parameter is rejected with 422."""
    assert client.get("/v1/medications/search").status_code == 422


def test_empty_query_param_is_422(client: TestClient) -> None:
    # min_length=1 on the query rejects an empty string at the boundary.
    """A medication search with an empty q is rejected with 422."""
    assert client.get("/v1/medications/search", params={"q": ""}).status_code == 422
