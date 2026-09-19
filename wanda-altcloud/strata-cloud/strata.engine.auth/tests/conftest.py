"""Shared fixtures: test settings, the Cognito seam faked, and the shared-DB fixtures.

No live AWS in any test. DB-touching tests are marked ``integration`` (Docker
required) and run on an ephemeral testcontainers Postgres with the identity
schema created the same way ``strata.identity``'s own suite does.
"""

import asyncio
import os

# Settings are read at import time; provide deterministic test values before the
# app imports (real env vars / .env values would otherwise leak into the suite).
os.environ["STRATA_COGNITO_REGION"] = "eu-west-2"
os.environ["STRATA_COGNITO_USER_POOL_ID"] = "eu-west-2_TESTPOOL"
os.environ["STRATA_COGNITO_CLIENT_ID"] = "test-client-id"
os.environ["STRATA_AUTH_MODE"] = "cognito"
os.environ.pop("STRATA_COGNITO_CLIENT_SECRET", None)
os.environ.pop("STRATA_AWS_PROFILE", None)
# No legacy endpoint in the suite's environment: the app's default legacy client
# is None (migration branch disabled) and tests inject stubs per-case. Set to ""
# (disabled), not popped — pydantic-settings also reads the developer's .env
# FILE, which popping os.environ cannot reach; an explicit empty env var wins.
for _legacy_var in ("STRATA_LEGACY_API_BASE_URL", "STRATA_LEGACY_API_KEY"):
    os.environ[_legacy_var] = ""
# Pin the pool-policy mirror to the Cognito defaults for the same reason — the
# suites assert the exact policy the 409 challenge echoes.
os.environ["STRATA_PASSWORD_MIN_LENGTH"] = "8"
for _policy_var in ("UPPERCASE", "LOWERCASE", "DIGIT", "SYMBOL"):
    os.environ[f"STRATA_PASSWORD_REQUIRE_{_policy_var}"] = "true"
# A dummy URL for non-DB tests: the lifespan creates the engine but nothing
# connects until a route actually uses a session. Integration fixtures override it.
os.environ["STRATA_DATABASE_URL"] = "postgresql+asyncpg://dummy:dummy@localhost:9/dummy"

from collections.abc import AsyncIterator, Iterator  # noqa: E402
from typing import Any  # noqa: E402

import pytest  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool  # noqa: E402
from strata_core.testing import migrated_database  # noqa: E402
from strata_identity.security import Principal  # noqa: E402

# The shipped identity testing helpers — one implementation of the token forge
# and the JWKS stub, shared with the strata.identity suite so this service's
# fakes can never drift. Databases are bootstrapped with the canonical
# migrations (strata_core.migrations), which also seed the role catalogue.
from strata_identity.testing import (  # noqa: E402
    generate_rsa_key,
    local_jwks_client,
    mint_cognito_token,
)
from testcontainers.postgres import PostgresContainer  # noqa: E402

from strata_engine_auth.core.auth import seam  # noqa: E402
from strata_engine_auth.db.session import get_session  # noqa: E402
from strata_engine_auth.main import create_app  # noqa: E402
from strata_engine_auth.services import cognito  # noqa: E402

LOGIN_RESULT = {
    "AuthenticationResult": {
        "AccessToken": "access-tok",
        "IdToken": "id-tok",
        "RefreshToken": "refresh-tok",
        "ExpiresIn": 3600,
    }
}
# Cognito does NOT return a new refresh token in the refresh flow.
REFRESH_RESULT = {
    "AuthenticationResult": {
        "AccessToken": "access-tok-2",
        "IdToken": "id-tok-2",
        "ExpiresIn": 3600,
    }
}


@pytest.fixture(autouse=True)
def fake_cognito(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fake the Cognito seam at module level (TESTING.md: no live AWS in any test)."""
    monkeypatch.setattr(cognito, "login", lambda identifier, password: LOGIN_RESULT)
    monkeypatch.setattr(cognito, "refresh", lambda refresh_token, sub=None: REFRESH_RESULT)


@pytest.fixture
def app() -> FastAPI:
    return create_app()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


ISSUER = "https://cognito-idp.eu-west-2.amazonaws.com/eu-west-2_TESTPOOL"  # test settings


@pytest.fixture(scope="session")
def private_key() -> RSAPrivateKey:
    return generate_rsa_key()


@pytest.fixture(autouse=True)
def stub_jwks(monkeypatch: pytest.MonkeyPatch, private_key: RSAPrivateKey) -> None:
    """No network in tests: the seam's JWKS client returns the local test key."""
    monkeypatch.setattr(seam._verifier, "_jwks_client", local_jwks_client(private_key))


def make_token(private_key: RSAPrivateKey, **overrides: Any) -> str:
    """A locally signed RS256 token shaped like a savanna-dev access token."""
    return mint_cognito_token(
        private_key,
        sub="sub-1",
        client_id="test-client-id",
        issuer=ISSUER,
        groups=["coach"],
        extra_claims=overrides,
    )


def override_principal(app: FastAPI, roles: list[str], email: str | None = None) -> None:
    """Bypass token verification with a known principal (roles as catalogue names)."""

    def _principal() -> Principal:
        return Principal(sub="sub-1", roles=roles, email=email, token="access-tok")

    app.dependency_overrides[seam.current_principal] = _principal


# --- the shared database (integration tests) --------------------------------------


@pytest.fixture(scope="session")
def pg_container() -> Iterator[PostgresContainer]:
    with PostgresContainer("postgres:16", driver="asyncpg") as pg:
        yield pg


@pytest.fixture
def db_engine(pg_container: PostgresContainer) -> Iterator[AsyncEngine]:
    """An engine on the ephemeral Postgres, migrated to head (catalogue seeded).

    NullPool so connections never outlive the event loop that opened them
    (setup/teardown run in their own loops; requests run in TestClient's).
    The canonical migrations replay per test — measured negligible.
    """
    url = str(pg_container.get_connection_url())
    with migrated_database(url):
        engine = create_async_engine(url, poolclass=NullPool)
        yield engine
        asyncio.run(engine.dispose())


@pytest.fixture
def db_sessionmaker(db_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(db_engine, expire_on_commit=False)


@pytest.fixture
def db_client(
    app: FastAPI, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> Iterator[TestClient]:
    """A client whose get_session dependency is bound to the ephemeral Postgres."""

    async def _session() -> AsyncIterator[AsyncSession]:
        async with db_sessionmaker() as session:
            yield session

    app.dependency_overrides[get_session] = _session
    with TestClient(app) as c:
        yield c


def run_db(coro: Any) -> Any:
    """Run a DB coroutine from a sync test (its own loop; NullPool keeps this safe)."""
    return asyncio.run(coro)
