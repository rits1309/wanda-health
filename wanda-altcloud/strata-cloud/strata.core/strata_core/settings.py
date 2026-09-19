"""Typed settings, `STRATA_` prefix (backend baseline convention).

A database library has exactly one setting: where the database is. Service
settings stay in the services; the tooling here (Alembic env, seed runner)
constructs :class:`Settings` at its entry point — pass ``_env_file=".env"``
there when the developer runbook flows through a dotenv file.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # extra="ignore": .env files and environments legitimately hold other vars.
    model_config = SettingsConfigDict(env_prefix="STRATA_", extra="ignore")

    database_url: str
    """Async SQLAlchemy URL, e.g. ``postgresql+asyncpg://user:pass@localhost:5432/db``."""
