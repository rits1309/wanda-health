"""Shared fixtures — ephemeral Postgres via testcontainers (Docker required).

A session-scoped real Postgres, built the canonical way: the
**canonical migrations** from ``strata-core`` create the schema (and seed the
role catalogue), then the ``booking-acceptance`` fixture profile loads the
deterministic baseline (the demo cast under booking's historical ids,
programmes, policies, patterns, example bookings) so every suite — including
the contract fuzz run — has real identities to act as. A NullPool engine keeps
connections from being shared across the per-test event loops; the app's
``get_session`` dependency is pinned to the test factory. Env defaults are set
before the app imports so ``Settings()`` can instantiate without a local
``.env``.
"""

import os

# Must be set before strata_booking.core.config imports (Settings instantiates at import).
os.environ.setdefault("STRATA_DATABASE_URL", "postgresql+asyncpg://unused:unused@localhost/unused")
os.environ.setdefault("STRATA_DEV_MODE", "true")
os.environ.setdefault("STRATA_DEV_AUTH_SECRET", "test-only-secret-0123456789abcdef")
# The shared seam: dev mode only starts when the environment is EXPLICITLY local.
os.environ.setdefault("STRATA_AUTH_MODE", "dev")
os.environ.setdefault("STRATA_ENVIRONMENT", "local")

import asyncio  # noqa: E402
from collections.abc import AsyncIterator, Iterator  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool  # noqa: E402
from strata_core.fixtures import load_profile  # noqa: E402
from strata_core.migrations import upgrade_to_head  # noqa: E402

from strata_booking.core.config import settings  # noqa: E402
from strata_booking.db import session as db_session  # noqa: E402
from strata_booking.main import create_app  # noqa: E402


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="session", autouse=True)
def _database() -> Iterator[AsyncEngine]:
    """Start Postgres, replay the canonical migrations, load the profile.

    Imported lazily so collection doesn't require Docker.
    """
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16", driver="asyncpg") as pg:
        settings.database_url = pg.get_connection_url()
        upgrade_to_head(settings.database_url)  # the one canonical history
        # hide_parameters mirrors the production engine (init_engine) so the log-safety
        # baseline gate exercises the same posture the service ships with.
        engine = create_async_engine(
            settings.database_url, poolclass=NullPool, hide_parameters=True
        )

        asyncio.run(load_profile(engine, "booking-acceptance"))
        try:
            yield engine
        finally:
            asyncio.run(engine.dispose())


@pytest.fixture(autouse=True)
def _pin_session_factory(_database: AsyncEngine) -> None:
    """Point the app's ``get_session`` dependency at the NullPool test factory before each test."""
    db_session._sessionmaker = async_sessionmaker(_database, expire_on_commit=False)


@pytest.fixture
async def session(_database: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """A per-test session whose writes can never leak into the shared database.

    The test runs inside an outer transaction on a dedicated connection; the session
    joins it in ``create_savepoint`` mode, so every ``commit()`` — the test's own or a
    service's internal one — releases a savepoint instead of ending the transaction.
    The outer transaction is rolled back when the test ends, discarding everything.
    Suite order therefore cannot matter for anything driven through this fixture.
    """
    async with _database.connect() as connection:
        outer = await connection.begin()
        s = AsyncSession(
            bind=connection,
            join_transaction_mode="create_savepoint",
            expire_on_commit=False,
        )
        try:
            yield s
        finally:
            await s.close()
            if outer.is_active:
                await outer.rollback()


@pytest.fixture
def client() -> Iterator[TestClient]:
    """In-process client driving the real ASGI app — no network, no server.

    Constructed without the lifespan context: the engine/session factory is owned by the
    session-scoped ``_database`` fixture, so the app must not create or dispose its own.
    """
    yield TestClient(create_app())
