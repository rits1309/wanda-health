"""Async engine + session factory, and the FastAPI session dependency.

The engine is created once at application startup (``app.main`` lifespan) so the connection
URL is read from settings *at runtime* — this is what lets the test suite point the service
at an ephemeral Postgres before the app starts, and what lets AWS inject
``STRATA_DATABASE_URL`` from Secrets Manager in Phase 2. The booking service's pattern.
"""

from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings

# Set by ``init_engine`` at startup. Module-level so the dependency can reach it without
# threading app state through every call.
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def init_engine(**engine_overrides: Any) -> AsyncEngine:
    """Create the async engine + session factory from settings; return the engine.

    Called once from the app lifespan; held by the caller for shutdown disposal. The test
    suite calls this too (with a NullPool override) so the baseline log-safety gate
    exercises the REAL engine construction — a statement-echo regression here turns
    tests/test_baseline.py red.
    """
    global _sessionmaker
    engine = create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        #: SQLAlchemy embeds bound parameters — clinical values on this service
        # in exception strings, which reach logs and the queue's last_error column via the
        # consumer's failure handling. Hide them; the SQL statement shape still renders.
        hide_parameters=True,
        **engine_overrides,
    )
    _sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    return engine


async def get_session() -> AsyncIterator[AsyncSession]:
    """Yield a database session for the lifetime of a request."""
    async with session_factory()() as session:
        yield session


def session_factory() -> async_sessionmaker[AsyncSession]:
    """The initialised session factory — for non-request contexts (e.g. the consumer loop)."""
    if _sessionmaker is None:
        raise RuntimeError("Database engine not initialised — it is created in the app lifespan.")
    return _sessionmaker
