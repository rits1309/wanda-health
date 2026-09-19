"""The credentialed admin client (Identity): profile plumbing, caching, and
the runtime/admin trust split. No live AWS (TESTING.md): building a boto3 client
makes no network calls and needs no credentials — only *using* one does — and the
profile-plumbing tests fake the session entirely.
"""

from typing import Any

import boto3
import pytest
from botocore import UNSIGNED

from strata_engine_auth.core.config import settings
from strata_engine_auth.services import cognito


@pytest.fixture(autouse=True)
def reset_cached_clients(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both module-level clients are lazy singletons — start each test cold."""
    monkeypatch.setattr(cognito, "_client", None)
    monkeypatch.setattr(cognito, "_admin_client", None)


class RecordingSession:
    """Stands in for boto3.Session: records construction + client() arguments."""

    instances: list["RecordingSession"] = []

    def __init__(self, profile_name: str | None = None) -> None:
        self.profile_name = profile_name
        self.client_calls: list[tuple[str, dict[str, Any]]] = []
        RecordingSession.instances.append(self)

    def client(self, service: str, **kwargs: Any) -> "RecordingSession":
        self.client_calls.append((service, kwargs))
        return self


@pytest.fixture
def recording_session(monkeypatch: pytest.MonkeyPatch) -> type[RecordingSession]:
    # cognito.py calls boto3.Session through the shared module object, so
    # patching it here is what its next call sees (restored per test).
    RecordingSession.instances = []
    monkeypatch.setattr(boto3, "Session", RecordingSession)
    return RecordingSession


def test_admin_client_uses_the_configured_profile(
    monkeypatch: pytest.MonkeyPatch, recording_session: type[RecordingSession]
) -> None:
    """STRATA_AWS_PROFILE selects a named developer session (never keys in config)."""
    monkeypatch.setattr(settings, "aws_profile", "sandbox")
    client = cognito.admin_cognito()
    (session,) = recording_session.instances
    assert session.profile_name == "sandbox"
    assert session.client_calls == [("cognito-idp", {"region_name": settings.cognito_region})]
    assert client is session


def test_admin_client_defaults_to_the_ambient_credential_chain(
    recording_session: type[RecordingSession],
) -> None:
    """Unset profile → the standard chain (developer session locally, IAM role deployed)."""
    assert settings.aws_profile is None  # conftest scrubs the env
    cognito.admin_cognito()
    (session,) = recording_session.instances
    assert session.profile_name is None


def test_admin_client_is_created_once(recording_session: type[RecordingSession]) -> None:
    """The credentialed admin client is a cached singleton - built once, reused."""
    assert cognito.admin_cognito() is cognito.admin_cognito()
    assert len(recording_session.instances) == 1


def test_admin_client_is_signed_and_separate_from_the_runtime_client() -> None:
    """The trust split: runtime login/refresh stays UNSIGNED; admin
    operations sign with real credentials. One client can never serve both."""
    runtime = cognito._cognito()
    admin = cognito.admin_cognito()
    assert admin is not runtime
    assert runtime.meta.config.signature_version is UNSIGNED
    assert admin.meta.config.signature_version is not UNSIGNED
