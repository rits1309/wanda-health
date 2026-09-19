"""Shared fixtures.

Tests run against an ephemeral PostgreSQL (testcontainers) seeded once from the committed,
source-shaped fixtures under ``tests/fixtures/``. This exercises the real SQL path (pg_trgm
search, JSONB, joins) while staying deterministic — the seed is fixed, so row counts and
rankings are stable. A NullPool engine is used so connections are never shared across the
per-test event loops anyio creates.

Auth: the suite runs against the shared seam's dev verifier (strata.identity), so env
defaults are set before the app imports — dev mode only starts when the environment is
EXPLICITLY local. ``client`` carries a valid dev bearer token by default (this service
accepts any authenticated user, so the role is incidental); ``anon_client`` sends no token
— use it for the 401 guard tests.
"""

import os

# Must be set before strata_terminology.core.config imports (Settings instantiates at import).
os.environ.setdefault("STRATA_DATABASE_URL", "postgresql+asyncpg://unused:unused@localhost/unused")
os.environ.setdefault("STRATA_DEV_AUTH_SECRET", "test-only-secret-0123456789abcdef")
os.environ.setdefault("STRATA_AUTH_MODE", "dev")
# The shared seam: dev mode only starts when the environment is EXPLICITLY local.
os.environ.setdefault("STRATA_ENVIRONMENT", "local")

import asyncio  # noqa: E402
from collections.abc import AsyncIterator, Iterator  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool  # noqa: E402
from strata_core.migrations import upgrade_to_head  # noqa: E402
from strata_identity.roles import ROLE_COACH  # noqa: E402
from strata_identity.security import mint_dev_token  # noqa: E402

from scripts.ingest_diagnoses import ingest as ingest_diagnoses  # noqa: E402
from scripts.ingest_drugs import ingest  # noqa: E402
from scripts.ingest_procedures import ingest as ingest_procedures  # noqa: E402
from strata_terminology.core.config import settings  # noqa: E402
from strata_terminology.db import session as db_session  # noqa: E402
from strata_terminology.main import create_app  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
FIXTURE = FIXTURES / "sample-ndc.json"
PCS_FIXTURE = FIXTURES / "sample-icd10pcs.txt"
CM_FIXTURE = FIXTURES / "sample-icd10cm.txt"


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="session", autouse=True)
def _database() -> Iterator[async_sessionmaker[AsyncSession]]:
    """Start Postgres, create the schema + pg_trgm, seed the fixtures; yield the factory.

    A NullPool engine is used so the factory is reusable across the many short-lived event
    loops the test run creates (anyio per-test loops, TestClient's portal, schemathesis').
    Imported lazily so collection doesn't require Docker.
    """
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16", driver="asyncpg") as pg:
        settings.database_url = pg.get_connection_url()
        upgrade_to_head(settings.database_url)  # the canonical migrations own the schema
        engine = create_async_engine(settings.database_url, poolclass=NullPool)
        factory = async_sessionmaker(engine, expire_on_commit=False)

        async def _provision() -> None:
            await ingest(FIXTURE)
            await ingest_procedures(PCS_FIXTURE)
            await ingest_diagnoses(CM_FIXTURE)

        asyncio.run(_provision())
        try:
            yield factory
        finally:
            asyncio.run(engine.dispose())


@pytest.fixture(autouse=True)
def _pin_session_factory(_database: async_sessionmaker[AsyncSession]) -> None:
    """Point the app's ``get_session`` dependency at the NullPool test factory before each test.

    The contract suite drives the app's lifespan, which calls ``init_engine`` and replaces the
    global factory with a default-pool engine bound to a transient loop (then disposes it).
    Re-pinning here keeps every test on the durable NullPool factory.
    """
    db_session._sessionmaker = _database


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """An async session for unit tests that drive the seam directly."""
    assert db_session._sessionmaker is not None
    async with db_session._sessionmaker() as s:
        yield s


@pytest.fixture
def auth_headers() -> dict[str, str]:
    """A valid dev bearer token — any authenticated user passes on this service."""
    assert settings.dev_auth_secret is not None
    token = mint_dev_token(settings.dev_auth_secret, "t-1", [ROLE_COACH])
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def client(auth_headers: dict[str, str]) -> Iterator[TestClient]:
    """In-process client driving the real ASGI app — no network, no server; authenticated.

    Constructed without the lifespan context: the engine/session factory is owned by the
    session-scoped ``_database`` fixture, so the app must not create or dispose its own.
    """
    c = TestClient(create_app())
    c.headers.update(auth_headers)
    yield c


@pytest.fixture
def anon_client() -> Iterator[TestClient]:
    """The same in-process client with no bearer token — for the 401 guard tests."""
    yield TestClient(create_app())
