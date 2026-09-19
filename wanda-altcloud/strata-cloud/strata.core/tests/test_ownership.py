"""Ownership is mechanically checkable.

Every table registered on the canonical metadata must appear in the ownership
registry with a known writing service — a new table without an owner fails CI.
"""

import re
from pathlib import Path

import strata_core
import strata_core.domains  # noqa: F401  — registers every canonical model
from strata_core.db.base import Base
from strata_core.ownership import DOMAIN_WRITERS, TABLE_DOMAINS, writer_for

KNOWN_SERVICES = {
    "strata.terminology",
    "strata.engine.auth",
    "strata.booking",
    "strata.connect",
    # The clinical domain's writer is deliberately a service that does not exist
    # yet ( option-5 separability; canonical name is) — the registry
    # records the forward boundary, and this entry is replaced when it ships.
    "member-profile service (future)",
}

OWNERSHIP_DOC = Path(strata_core.__file__).parent / "OWNERSHIP.md"


def test_every_canonical_table_has_an_owner() -> None:
    """Every table in Base.metadata has a domain owner in TABLE_DOMAINS, and nothing extra."""
    assert set(Base.metadata.tables) == set(TABLE_DOMAINS)


def test_every_domain_writer_is_a_known_service() -> None:
    """Every domain maps to a writer and every writer is a known service."""
    assert set(DOMAIN_WRITERS.values()) <= KNOWN_SERVICES
    assert set(TABLE_DOMAINS.values()) == set(DOMAIN_WRITERS)


def test_writer_lookup() -> None:
    """writer_for resolves representative tables to their owning services."""
    assert writer_for("user_profiles") == "strata.engine.auth"
    # kernel.identifiers: the identity write path
    # every mapping write (Cognito adoption, device registration, the legacy
    # import) goes through strata.engine.auth's owned seam code.
    assert writer_for("user_external_ids") == "strata.engine.auth"
    assert writer_for("user_external_id_types") == "strata.engine.auth"
    assert writer_for("programmes") == "strata.booking"
    assert writer_for("products") == "strata.terminology"
    assert writer_for("weight_readings") == "strata.connect"


def _documented_tables() -> set[str]:
    """Table names in the Tables column of the OWNERSHIP.md registry table.

    Only that column is read (table names also appear in the Notes column and as
    prose elsewhere), so the parse cannot pick up non-table identifiers.
    """
    tables: set[str] = set()
    tables_col: int | None = None
    for line in OWNERSHIP_DOC.read_text().splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if tables_col is None:
            if "Tables" in cells:
                tables_col = cells.index("Tables")
            continue
        if set("".join(cells)) <= {"-", ":"}:  # markdown separator row
            continue
        if tables_col < len(cells):
            tables |= set(re.findall(r"`([a-z0-9_]+)`", cells[tables_col]))
    return tables


def test_ownership_doc_lists_every_canonical_table() -> None:
    """OWNERSHIP.md (the human half) stays in step with ownership.py (the code).

    Closes the metadata == code == doc chain: the metadata check above ties code
    to metadata; this ties the doc to code, so a table added to the registry
    without a matching OWNERSHIP.md row (or a stale row) fails the build.
    """
    documented = _documented_tables()
    assert documented == set(TABLE_DOMAINS), (
        "OWNERSHIP.md is out of step with ownership.py; reconcile its Tables column "
        f"(missing from doc: {sorted(set(TABLE_DOMAINS) - documented)}; "
        f"stale in doc: {sorted(documented - set(TABLE_DOMAINS))})"
    )
