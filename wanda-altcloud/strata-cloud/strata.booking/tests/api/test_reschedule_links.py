"""API tests for the public tokened reschedule resolver + the dev jobs trigger."""

from datetime import timedelta

from fastapi.testclient import TestClient

from strata_booking.services.tokens import mint_link_token


def test_invalid_reschedule_token_is_404(client: TestClient) -> None:
    """A garbage reschedule token is 404."""
    assert client.get("/v1/reschedule", params={"token": "garbage"}).status_code == 404


def test_wrong_purpose_reschedule_token_is_404(client: TestClient) -> None:
    """A token minted for a different purpose (alternatives) is 404 on reschedule."""
    token = mint_link_token("alternative", "some-id", timedelta(days=1))
    assert client.get("/v1/reschedule", params={"token": token}).status_code == 404


def test_unknown_reference_is_404(client: TestClient) -> None:
    """A reschedule token pointing at a missing record is 404."""
    token = mint_link_token("reschedule", "does-not-exist", timedelta(days=1))
    assert client.get("/v1/reschedule", params={"token": token}).status_code == 404


def test_dev_jobs_run_returns_counts(client: TestClient) -> None:
    """The dev jobs trigger runs every job and reports each one's counts."""
    resp = client.post("/v1/dev/jobs/run")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {
        "slots_created",
        "reminders_sent",
        "reminders_cancelled",
        "no_shows_detected",
        "appointments_completed",
    }
    assert all(isinstance(v, int) and v >= 0 for v in body.values())
