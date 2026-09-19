"""The enriched /v1/auth/me under strict resolution, against a real (ephemeral) Postgres.

Fail-hard is the rule: a verified subject resolves through
the identity seam — the active ``Cognito Sub`` mapping first, the profile id
itself for dev tokens — or the request is rejected and NOTHING is created.
Lazy provisioning and self-signup died at Identity (their absence is
pinned below).
"""

from datetime import UTC, datetime

import pytest
from conftest import make_token, override_principal, run_db
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from strata_core.domains.kernel import (
    COGNITO_SUB,
    EXTERNAL_ID_TYPE_IDS,
    UserExternalId,
    UserLanguage,
    UserProfile,
)

pytestmark = pytest.mark.integration


async def _profile_count(sessionmaker: async_sessionmaker[AsyncSession]) -> int:
    async with sessionmaker() as session:
        return (await session.execute(select(func.count()).select_from(UserProfile))).scalar_one()


async def _insert_profile(
    sessionmaker: async_sessionmaker[AsyncSession], *, profile_id: str | None = None
) -> str:
    """A profile with an active ``Cognito Sub`` mapping for the test subject."""
    async with sessionmaker() as session:
        profile = UserProfile(
            email="lewis@wandahealth.com",
            first_name="Lewis",
            last_name="K",
            timezone="Europe/London",
        )
        if profile_id:
            profile.id = profile_id
        session.add(profile)
        await session.flush()
        session.add(
            UserExternalId(
                user_id=profile.id,
                type_id=EXTERNAL_ID_TYPE_IDS[COGNITO_SUB],
                external_id="sub-1",
                registered_at=datetime.now(UTC),
            )
        )
        session.add(UserLanguage(user_id=profile.id, language_code="en"))
        await session.commit()
        return profile.id


def test_me_returns_the_enriched_principal(
    app: FastAPI, db_client: TestClient, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """GET /v1/auth/me returns the profile-enriched principal, keyed by profile id with no
    cognito_sub."""
    profile_id = run_db(_insert_profile(db_sessionmaker))
    override_principal(app, roles=["Admin", "Coach"])  # multi-role
    response = db_client.get("/v1/auth/me", headers={"Authorization": "Bearer x"})
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == profile_id  # the identifier clients key on (identity-mapping net)
    assert "cognito_sub" not in body  # left the contract at — a DELIBERATE net update
    assert body["display_name"] == "Lewis K"
    assert body["timezone"] == "Europe/London"
    assert body["languages"] == ["en"]
    assert body["roles"] == ["Admin", "Coach"]


def test_me_with_a_real_token_end_to_end(
    db_client: TestClient,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    private_key: RSAPrivateKey,
) -> None:
    """Full path: RS256 token → shared seam → groups→roles → mapping resolution → profile."""
    profile_id = run_db(_insert_profile(db_sessionmaker))
    token = make_token(private_key)  # sub-1; cognito:groups ["coach"]; stub_jwks is autouse
    response = db_client.get("/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == profile_id
    assert body["roles"] == ["Coach"]


def test_me_unmapped_subject_fails_hard_and_creates_nothing(
    app: FastAPI, db_client: TestClient, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """no active mapping, no profile-id match → rejected; no lazy
    provisioning — the table stays empty."""
    override_principal(app, roles=["Coach"])
    response = db_client.get("/v1/auth/me", headers={"Authorization": "Bearer x"})
    assert response.status_code == 403
    assert "profile" in response.json()["detail"].lower()
    assert run_db(_profile_count(db_sessionmaker)) == 0


def test_me_resolves_a_dev_profile_id_subject(
    app: FastAPI, db_client: TestClient, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """Dev tokens are historically minted with sub = the profile id: the seam's
    fallback resolves them with no mapping row (— the unified rule)."""

    async def _bare_profile() -> None:
        async with db_sessionmaker() as session:
            session.add(
                UserProfile(
                    id="sub-1",  # the override principal's sub IS the profile id
                    email="dev@wandahealth.com",
                    first_name="Dev",
                    last_name="Cast",
                    timezone="Etc/UTC",
                )
            )
            await session.commit()

    run_db(_bare_profile())
    override_principal(app, roles=["Coach"])
    response = db_client.get("/v1/auth/me", headers={"Authorization": "Bearer x"})
    assert response.status_code == 200
    assert response.json()["id"] == "sub-1"


def test_me_wire_shape_is_the_sd2_contract(
    app: FastAPI, db_client: TestClient, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """/me carries exactly the enriched-principal field set — the raw editable
    fields the profile page needs (first/last, the nullable override, preferred
    language) alongside the effective display_name, and never the cognito_sub."""
    run_db(_insert_profile(db_sessionmaker))
    override_principal(app, roles=["Coach"])
    body = db_client.get("/v1/auth/me", headers={"Authorization": "Bearer x"}).json()
    assert set(body) == {
        "id",
        "email",
        "first_name",
        "last_name",
        "display_name",
        "display_name_override",
        "timezone",
        "preferred_language",
        "languages",
        "roles",
    }
    assert (body["first_name"], body["last_name"]) == ("Lewis", "K")
    assert body["display_name"] == "Lewis K"  # effective (no override)
    assert body["display_name_override"] is None
    assert body["preferred_language"] is None


_VALID_PATCH = {
    "first_name": "Thomas",
    "last_name": "Smith",
    "display_name": "Tom",
    "timezone": "America/Chicago",
    "languages": ["en", "es"],
    "preferred_language": "es",
}


def test_update_me_persists_the_save(
    app: FastAPI, db_client: TestClient, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """PATCH /v1/auth/me persists the Save to the canonical profile and returns the
    fresh enriched principal."""
    profile_id = run_db(_insert_profile(db_sessionmaker))
    override_principal(app, roles=["Coach"])
    response = db_client.patch(
        "/v1/auth/me", json=_VALID_PATCH, headers={"Authorization": "Bearer x"}
    )
    assert response.status_code == 200
    body = response.json()
    assert (body["first_name"], body["last_name"]) == ("Thomas", "Smith")
    assert body["display_name"] == "Tom"
    assert body["display_name_override"] == "Tom"
    assert body["timezone"] == "America/Chicago"
    assert body["preferred_language"] == "es"
    assert body["languages"] == ["en", "es"]

    async def _reload() -> tuple[str, str, str | None]:
        async with db_sessionmaker() as session:
            profile = await session.get(UserProfile, profile_id)
            assert profile is not None
            return profile.first_name, profile.timezone, profile.preferred_language_code

    assert run_db(_reload()) == ("Thomas", "America/Chicago", "es")


@pytest.mark.parametrize(
    ("override", "fragment"),
    [
        ({"first_name": " "}, ""),
        ({"languages": [], "preferred_language": None}, ""),
        ({"languages": ["en", "fr"], "preferred_language": "en"}, ""),
        ({"languages": ["en"], "preferred_language": "es"}, ""),
        ({"timezone": "Europe/Madrid"}, ""),
    ],
)
def test_update_me_rejects_invalid_payloads(
    app: FastAPI,
    db_client: TestClient,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    override: dict[str, object],
    fragment: str,
) -> None:
    """Each domain invariant is a 422 (2/3/9), enforced by the seam."""
    run_db(_insert_profile(db_sessionmaker))
    override_principal(app, roles=["Coach"])
    response = db_client.patch(
        "/v1/auth/me",
        json={**_VALID_PATCH, **override},
        headers={"Authorization": "Bearer x"},
    )
    assert response.status_code == 422
    assert fragment in response.json()["detail"]


def test_update_me_unmapped_subject_is_403_and_creates_nothing(
    app: FastAPI, db_client: TestClient, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """A verified subject with no profile cannot Save one into existence."""
    override_principal(app, roles=["Coach"])
    response = db_client.patch(
        "/v1/auth/me", json=_VALID_PATCH, headers={"Authorization": "Bearer x"}
    )
    assert response.status_code == 403
    assert run_db(_profile_count(db_sessionmaker)) == 0


def test_languages_returns_the_catalogue(
    app: FastAPI, db_client: TestClient, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """GET /v1/auth/languages serves the catalogue codes, read from
    the DB so a future migration surfaces without a code change."""
    override_principal(app, roles=["Coach"])
    response = db_client.get("/v1/auth/languages", headers={"Authorization": "Bearer x"})
    assert response.status_code == 200
    assert response.json() == ["en", "es"]  # catalogue rows, ordered


def test_languages_requires_authentication(db_client: TestClient) -> None:
    """The catalogue is behind the same bearer guard as /me: no token, no read."""
    assert db_client.get("/v1/auth/languages").status_code == 401


def test_signup_and_confirm_are_retired(client: TestClient) -> None:
    """no self-signup — the endpoints are gone, not disabled."""
    assert client.post("/v1/auth/signup", json={}).status_code == 404
    assert client.post("/v1/auth/confirm", json={}).status_code == 404
