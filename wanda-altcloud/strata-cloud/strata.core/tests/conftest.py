"""Shared fixtures: an ephemeral testcontainers Postgres for DB-touching tests.

DB tests are marked ``integration`` (Docker required); ``-m "not integration"``
runs the Docker-free subset. Databases are built the way consumers build them —
by running the canonical migrations — never via ``create_all``.
"""

from collections.abc import AsyncIterator, Iterator

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from testcontainers.postgres import PostgresContainer

from strata_core.testing import migrated_database


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
    """The ephemeral database at head — torn back down to base afterwards."""
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
