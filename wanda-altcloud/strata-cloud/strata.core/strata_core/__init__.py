"""strata-core — the canonical database project for the Savanna operational store.

All operational schema, the single Alembic migration history, and the fixture
library live here and nowhere else;
services consume this package and carry no migrations of their own.

Build-out: adds the canonical models + migration baseline,
the fixture profiles. Until then the package exposes only its settings.
"""
