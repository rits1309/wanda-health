"""Shipped test-bootstrap helper — the canonical test-database lifecycle, defined once.

Consumer suites (and this project's own) build ephemeral test databases the
canonical way: replay the migration history to head, use the database, tear it
back down. This is the single implementation of that lifecycle, so suites can't
drift on how a test database is built (the scope — per test vs per session — is
each suite's own choice; session-scoped suites that never tear down may call
``strata_core.migrations.upgrade_to_head`` directly).

Dependency-light on purpose (the ``strata_identity.testing`` pattern): no
pytest, no testcontainers — callers bring their own container and wrap this in
their own fixtures. Sync only: the alembic env drives its own event loop.
"""

from collections.abc import Iterator
from contextlib import contextmanager

from strata_core.migrations import downgrade_to_base, upgrade_to_head

__all__ = ["migrated_database"]


@contextmanager
def migrated_database(database_url: str) -> Iterator[str]:
    """Migrate ``database_url`` to head (catalogue seeded); tear back down on exit."""
    upgrade_to_head(database_url)
    try:
        yield database_url
    finally:
        downgrade_to_base(database_url)
