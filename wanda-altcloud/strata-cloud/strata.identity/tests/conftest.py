"""Shared fixtures: an ephemeral testcontainers Postgres for DB-touching tests.

DB tests are marked ``integration`` (Docker required); ``-m "not integration"``
runs the Docker-free subset. Databases are bootstrapped the canonical way —
by replaying ``strata-core``'s migrations — which also seeds the
role catalogue; ``create_all`` is gone with the absorbed identity chain.
"""

from collections.abc import AsyncIterator, Iterator

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from strata_core.testing import migrated_database
from testcontainers.postgres import PostgresContainer


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="session")
def pg_container() -> Iterator[PostgresContainer]:
    with PostgresContainer("postgres:16", driver="asyncpg") as pg:
        yield pg


@pytest.fixture(scope="session")
def database_url(pg_container: PostgresContainer) -> str:
    return str(pg_container.get_connection_url())


@pytest.fixture
def migrated_database_url(database_url: str) -> Iterator[str]:
    """The ephemeral database at head (catalogue seeded) — torn back down afterwards."""
    with migrated_database(database_url) as url:
        yield url


@pytest.fixture
async def engine(migrated_database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(migrated_database_url)
    yield engine
    await engine.dispose()


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        yield s
