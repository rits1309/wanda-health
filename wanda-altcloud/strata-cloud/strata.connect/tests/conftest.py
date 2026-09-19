"""Shared fixtures — ephemeral Postgres via testcontainers (Docker required).

The booking service's pattern: a session-scoped real Postgres built the canonical way — the
**canonical migrations** from ``strata-core`` create the schema (and seed the external-ID
type catalogue), then tests create the people they need via ``strata_core.fixtures`` (dev
databases seed the ``readings-demo`` profile instead — landed at). A NullPool engine
keeps connections from being shared across per-test event loops; the app's ``get_session``
dependency is pinned to the
test factory. Env defaults are set before the app imports so ``Settings()`` can instantiate
without a local ``.env``. Tracing export is off by default (STRATA_OTEL_TRACES_EXPORTER=none).
"""

import os

# Must be set before app.config imports (Settings instantiates at import).
os.environ.setdefault("STRATA_DATABASE_URL", "postgresql+asyncpg://unused:unused@localhost/unused")
os.environ.setdefault("STRATA_EDGE_SHARED_SECRET", "test-only-edge-secret")
# Pinned EXPLICITLY, not setdefault: pydantic-settings also reads the developer's .env FILE,
# where STRATA_DEV_MODE=true is the documented local default — only a real env var outranks it.
# Tests that need the dev seam monkeypatch settings.dev_mode for their own app.
os.environ["STRATA_DEV_MODE"] = "false"
os.environ["STRATA_ENVIRONMENT"] = "local"
# Immediate retries suite-wide so retry-ladder tests don't wait; the delay semantics
# themselves are covered by a dedicated seam test with an explicit non-zero queue.
os.environ.setdefault("STRATA_PIPELINE_RETRY_DELAY_SECONDS", "0")

import asyncio  # noqa: E402
from collections.abc import AsyncIterator, Iterator  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402
from strata_core.migrations import upgrade_to_head  # noqa: E402

from app import db as app_db  # noqa: E402
from app.config import settings  # noqa: E402
from app.main import create_app  # noqa: E402

# Read back rather than repeated, in case the environment supplied its own value.
EDGE_SECRET = os.environ["STRATA_EDGE_SHARED_SECRET"]


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="session", autouse=True)
def _database() -> Iterator[async_sessionmaker[AsyncSession]]:
    """Start Postgres and replay the canonical migrations.

    Imported lazily so collection doesn't require Docker.
    """
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16", driver="asyncpg") as pg:
        settings.database_url = pg.get_connection_url()
        upgrade_to_head(settings.database_url)  # the one canonical history
        # Through the app's own init_engine (NullPool override only) so the baseline
        # log-safety gate tests the real engine construction.
        engine = app_db.init_engine(poolclass=NullPool)
        factory = app_db.session_factory()
        try:
            yield factory
        finally:
            asyncio.run(engine.dispose())


@pytest.fixture(autouse=True)
def _pin_session_factory(_database: async_sessionmaker[AsyncSession]) -> None:
    """Point the app's ``get_session`` dependency at the NullPool test factory."""
    app_db._sessionmaker = _database


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """An async session for tests that drive a service seam directly."""
    async with app_db.session_factory()() as s:
        yield s


@pytest.fixture
def client() -> Iterator[TestClient]:
    yield TestClient(create_app())


@pytest.fixture
async def drained(session: AsyncSession) -> None:
    """Retire ready queue entries other tests left behind, so dequeue-order-sensitive
    tests (seams, processor) are deterministic."""
    from sqlalchemy import update
    from strata_core.domains.device_readings import ReadingQueueEntry

    await session.execute(
        update(ReadingQueueEntry)
        .where(ReadingQueueEntry.state == "ready")
        .values(state="done", last_error="drained by conftest.drained")
    )
    await session.commit()
