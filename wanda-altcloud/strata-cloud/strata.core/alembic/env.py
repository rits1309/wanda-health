"""Alembic migration environment (async) for the canonical schema.

This is the **single** migration history for the operational store:
one linear chain, one head, the default ``alembic_version`` table in ``public``.
The identity chain it absorbed (``alembic_version_identity``) is retired at;
local databases are recreated from this baseline and re-seeded.

The connection URL comes from ``STRATA_DATABASE_URL`` when set (the same
variable every consumer uses for the shared database), falling back to the
alembic.ini default that matches the local shared Postgres.

Reflection is scoped to the default (``public``) schema so autogenerate on the
shared database never sees other schemas (e.g. ``identity`` while it still
exists) and never emits drops for tables it does not own.
"""

import asyncio
import os
from logging.config import fileConfig
from typing import Any

from alembic.script import ScriptDirectory
from sqlalchemy import inspect, pool, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

import strata_core.domains  # noqa: F401  — registers every canonical model on Base.metadata
from alembic import context
from strata_core.db.base import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# URL precedence: programmatic caller (Config.attributes — the supported channel,
# used by strata_core.migrations) > STRATA_DATABASE_URL > the alembic.ini default.
# `%` escaped for the ini layer's interpolating ConfigParser (URL-encoded credentials
# like `p%40ss` otherwise raise at set_main_option); get_* unescapes on the way out.
database_url = config.attributes.get("sqlalchemy_url") or os.environ.get("STRATA_DATABASE_URL")
if database_url:
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))

target_metadata = Base.metadata


def include_name(name: str | None, type_: str, parent_names: dict[str, Any]) -> bool:  # noqa: ARG001
    """Reflect only the default schema — the canonical chain owns exactly that."""
    if type_ == "schema":
        return name is None  # None = the connection's default schema (public)
    return True


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL, no DBAPI needed)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_name=include_name,
    )

    with context.begin_transaction():
        context.run_migrations()


def _preflight_known_history(connection: Connection) -> None:
    """Refuse to run against a database migrated by a retired pre-consolidation chain.

    The canonical history re-baselined the old per-service chains (engine, booking,
    identity) with no bridge — by design: local databases are recreated and re-seeded.
    Without this check the failure is Alembic's cryptic "Can't locate revision".

    Ends its read transaction (rollback) so Alembic owns the connection's
    transaction — a lingering autobegin would silently swallow the migration.
    """
    try:
        if not inspect(connection).has_table("alembic_version"):
            return
        row = connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one_or_none()
    finally:
        connection.rollback()
    if row is None:
        return
    script = ScriptDirectory.from_config(config)
    try:
        script.get_revisions(row)
    except Exception:
        raise SystemExit(
            f"This database was last migrated by a retired pre-consolidation history "
            f"(revision {row!r} is not in the canonical chain). Recreate and re-seed it: "
            f"`inv db-reset && inv migrate && inv seed --profile demo` from strata.core "
            f"(engine reference data must be re-ingested — see strata.core/README.md)."
        ) from None


def do_run_migrations(connection: Connection) -> None:
    _preflight_known_history(connection)
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_name=include_name,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Create an async Engine and associate a connection with the context."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
