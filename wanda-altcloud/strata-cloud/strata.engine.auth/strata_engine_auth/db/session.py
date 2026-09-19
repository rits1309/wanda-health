"""Async engine + session factory, and the FastAPI session dependency.

The engine is created once at application startup (``app.main`` lifespan) so the
connection URL is read from settings *at runtime* — tests point the service at an
ephemeral Postgres before the app starts; AWS injects ``STRATA_DATABASE_URL``.
The database is the SHARED instance whose ``identity`` schema is owned by
``strata.identity``'s migration chain — this service runs no migrations of its
own (it has no tables); apply identity migrations first (`inv migrate` in
``../strata.identity``).
"""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from strata_engine_auth.core.config import settings

# Set by ``init_engine`` at startup. Module-level so the dependency can reach it
# without threading app state through every call.
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def init_engine() -> AsyncEngine:
    """Create the async engine + session factory from settings; return the engine."""
    global _sessionmaker
    engine = create_async_engine(settings.database_url, echo=settings.db_echo, pool_pre_ping=True)
    _sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    return engine


async def get_session() -> AsyncIterator[AsyncSession]:
    """Yield a database session for the lifetime of a request."""
    if _sessionmaker is None:
        raise RuntimeError("Database engine not initialised — it is created in the app lifespan.")
    async with _sessionmaker() as session:
        yield session
