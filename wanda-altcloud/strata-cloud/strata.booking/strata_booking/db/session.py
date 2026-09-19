"""Async engine + session factory, and the FastAPI session dependency.

The engine is created once at application startup (``strata_booking.main`` lifespan) so the
connection URL is read from settings *at runtime* — this is what lets the test suite point the
service at an ephemeral Postgres before the app starts, and what lets AWS inject
``STRATA_DATABASE_URL`` from Secrets Manager. ``get_session`` is the dependency routes use to
obtain a session.
"""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from strata_booking.core.config import settings

# Set by ``init_engine`` at startup. Module-level so the dependency can reach it without
# threading app state through every call.
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def init_engine() -> AsyncEngine:
    """Create the async engine + session factory from settings; return the engine.

    Called once from the app lifespan. The returned engine is held by the caller so it can be
    disposed on shutdown.
    """
    global _sessionmaker
    # hide_parameters: clinical classification — bound parameters
    # (member↔programme linkage, member-supplied text) never render in exceptions or
    # SQL logging. Guarded by the baseline gate; do not remove.
    engine = create_async_engine(settings.database_url, pool_pre_ping=True, hide_parameters=True)
    _sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    return engine


async def get_session() -> AsyncIterator[AsyncSession]:
    """Yield a database session for the lifetime of a request."""
    async with session_factory()() as session:
        yield session


def session_factory() -> async_sessionmaker[AsyncSession]:
    """The initialised session factory — for non-request contexts (e.g. the jobs loop)."""
    if _sessionmaker is None:
        raise RuntimeError("Database engine not initialised — it is created in the app lifespan.")
    return _sessionmaker
