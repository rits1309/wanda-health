"""The legacy credentials client: the owner-relayed contract,
API-key plumbing, and no-verdict failures. No live network (TESTING.md) — every
test runs on an httpx MockTransport.
"""

import json
from typing import Any

import httpx
import pytest

from strata_engine_auth.core.config import Settings
from strata_engine_auth.services.legacy import (
    API_KEY_HEADER,
    HttpLegacyCredentialsClient,
    LegacyUnavailableError,
    create_legacy_client,
)

pytestmark = pytest.mark.anyio

CHECK_URL = "https://legacy.test/api/user/credentials/check/"


def _client_returning(
    body: Any, *, status: int = 200, api_key: str | None = None
) -> tuple[HttpLegacyCredentialsClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json=body)

    client = HttpLegacyCredentialsClient(
        check_url=CHECK_URL, api_key=api_key, transport=httpx.MockTransport(handler)
    )
    return client, seen


async def test_patient_acceptance_parses_id_and_kind() -> None:
    """A patient acceptance parses django_user_id, the patient kind, and the internal patient id."""
    client, seen = _client_returning(
        {"success": True, "django_user_id": 2403, "internal_patient_id": 1688}
    )
    verdict = await client.check("c149pn2db0b", "pw")
    assert (verdict.accepted, verdict.legacy_user_id, verdict.kind, verdict.internal_id) == (
        True,
        "2403",
        "patient",
        "1688",  # the Legacy Summit Patient ID value
    )
    assert json.loads(seen[0].content) == {"username": "c149pn2db0b", "password": "pw"}


async def test_coach_acceptance_parses_id_and_kind() -> None:
    """A coach acceptance parses django_user_id, the coach kind, and the internal coach id."""
    client, _ = _client_returning(
        {"success": True, "django_user_id": 2507, "internal_client_user_id": 743}
    )
    verdict = await client.check("coach@wandahealth.com", "pw")
    assert (verdict.accepted, verdict.legacy_user_id, verdict.kind, verdict.internal_id) == (
        True,
        "2507",
        "coach",
        "743",  # the Legacy Summit Coach ID value
    )


async def test_rejection_is_a_clean_verdict_not_an_error() -> None:
    """The platform's rejection is generic by design (no unknown-vs-wrong-password
    signal) and keys on `success`, whatever the HTTP status."""
    client, _ = _client_returning({"success": False, "error": "Invalid credentials"})
    verdict = await client.check("nobody", "wrong")
    assert not verdict.accepted and verdict.legacy_user_id is None


async def test_api_key_header_attached_only_when_configured() -> None:
    """The API-key header is sent only when a key is configured (production hardening)."""
    keyed, seen_keyed = _client_returning({"success": False, "error": ""}, api_key="prod-key")
    await keyed.check("u", "p")
    assert seen_keyed[0].headers[API_KEY_HEADER] == "prod-key"

    keyless, seen_keyless = _client_returning({"success": False, "error": ""})
    await keyless.check("u", "p")  # UAT: no request auth at all (owner, 21-07-2026)
    assert API_KEY_HEADER not in seen_keyless[0].headers


async def test_extra_fields_are_tolerated() -> None:
    """Unknown fields in the legacy response are tolerated - the contract is parsed, not pinned."""
    client, _ = _client_returning(
        {"success": True, "django_user_id": 9, "internal_patient_id": 1, "new_field": "x"}
    )
    assert (await client.check("u", "p")).accepted


async def test_transport_failure_raises_unavailable_without_echoing_the_request() -> None:
    """A transport failure raises LegacyUnavailableError carrying none of the
    request's credentials."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("boom")

    client = HttpLegacyCredentialsClient(
        check_url=CHECK_URL, transport=httpx.MockTransport(handler)
    )
    with pytest.raises(LegacyUnavailableError) as err:
        await client.check("secret-user", "secret-pass")
    # the error must not carry the credentials the request did.
    assert "secret-user" not in str(err.value) and "secret-pass" not in str(err.value)


async def test_malformed_body_is_no_verdict() -> None:
    """A malformed (non-object) body is no verdict - LegacyUnavailableError, never a guess."""
    client, _ = _client_returning(["not", "a", "dict"])
    with pytest.raises(LegacyUnavailableError):
        await client.check("u", "p")


async def test_acceptance_without_the_mapping_key_is_no_verdict() -> None:
    """An acceptance the gate cannot use must never pass as accepted."""
    client, _ = _client_returning({"success": True})
    with pytest.raises(LegacyUnavailableError):
        await client.check("u", "p")


def test_factory_disabled_without_a_base_url() -> None:
    """Without STRATA_LEGACY_API_BASE_URL the factory returns None and the migration branch is
    disabled."""
    assert create_legacy_client(Settings()) is None  # env has no STRATA_LEGACY_* (conftest)


def test_factory_joins_base_url_and_path() -> None:
    """The factory joins the base URL and the check path into the endpoint URL."""
    settings = Settings(legacy_api_base_url="https://legacy-platform.example.com/")
    client = create_legacy_client(settings)
    assert isinstance(client, HttpLegacyCredentialsClient)
    assert client._check_url == "https://legacy-platform.example.com/api/user/credentials/check/"
