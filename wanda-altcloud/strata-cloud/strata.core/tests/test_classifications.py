"""The classification catalog is complete, canonical, and vocabulary-bound.

The forcing function: a new column (or table) that reaches the canonical
metadata without a classification entry fails here — classification happens
in the same PR as the model and migration, never retrofitted.
"""

import strata_core.domains  # noqa: F401  — registers every canonical model
from strata_core.classifications import FIELD_CLASSIFICATIONS
from strata_core.db.base import Base
from strata_core.ownership import TABLE_DOMAINS

VOCABULARY = {"clinical", "personal", "internal"}


def test_every_canonical_column_is_classified() -> None:
    """Every canonical column carries a classification (completeness gate)."""
    unclassified = [
        f"{table}.{column.name}"
        for table in sorted(Base.metadata.tables)
        for column in Base.metadata.tables[table].columns
        if column.name not in FIELD_CLASSIFICATIONS.get(table, {})
    ]
    assert unclassified == [], f"unclassified columns (add to classifications.py): {unclassified}"


def test_no_orphan_classifications() -> None:
    """Every registry entry corresponds to a real canonical column — deletions
    and renames must update the catalog in the same change."""
    orphans = [
        f"{table}.{column}"
        for table, columns in FIELD_CLASSIFICATIONS.items()
        for column in columns
        if table not in Base.metadata.tables or column not in Base.metadata.tables[table].columns
    ]
    assert orphans == [], f"catalog entries with no matching column: {orphans}"


def test_vocabulary_is_adr_0056s() -> None:
    """Every classification value is one of 's three classes."""
    values = {cls for columns in FIELD_CLASSIFICATIONS.values() for cls in columns.values()}
    assert values <= VOCABULARY


def test_catalog_and_ownership_cover_the_same_tables() -> None:
    """The two registries walk together — a table cannot be owned but
    unclassified, or classified but ownerless."""
    assert set(FIELD_CLASSIFICATIONS) == set(TABLE_DOMAINS)
