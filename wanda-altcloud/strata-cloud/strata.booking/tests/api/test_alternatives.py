"""API tests for the public tokened alternatives endpoints."""

from fastapi.testclient import TestClient

from strata_booking.services.tokens import mint_link_token


def test_invalid_token_is_404(client: TestClient) -> None:
    """A garbage alternatives token is 404 on both describe and accept."""
    assert client.get("/v1/alternatives", params={"token": "garbage"}).status_code == 404
    assert client.post("/v1/alternatives/accept", json={"token": "garbage"}).status_code == 404


def test_wrong_purpose_token_is_404(client: TestClient) -> None:
    """A token minted for a different purpose (reschedule) is 404 on alternatives."""
    from datetime import timedelta

    token = mint_link_token("reschedule", "some-id", timedelta(days=1))
    assert client.get("/v1/alternatives", params={"token": token}).status_code == 404


def test_unknown_reference_is_404(client: TestClient) -> None:
    """An alternatives token pointing at a missing record is 404."""
    from datetime import timedelta

    token = mint_link_token("alternative", "does-not-exist", timedelta(days=1))
    assert client.get("/v1/alternatives", params={"token": token}).status_code == 404
