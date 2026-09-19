"""Render the Data Classification Register — the per-field audit projection.

The registry (``classifications.py``) is the source of truth; this module
renders it, joined with the canonical metadata and the ownership registry,
into ``DATA_CLASSIFICATION_REGISTER.md`` — the human-readable, per-column
audit document (the rule 6 projection pattern: generated FROM the
registry, drift-tested, never hand-edited).

Regenerate with ``inv classification-register`` after any schema or
classification change; ``tests/test_classification_register.py`` fails the
build while the committed register does not match the code.
"""

from datetime import date
from pathlib import Path

from alembic.script import ScriptDirectory
from sqlalchemy import Table

import strata_core.domains  # noqa: F401  — registers every canonical model
from strata_core.classifications import FIELD_CLASSIFICATIONS
from strata_core.db.base import Base
from strata_core.ownership import DOMAIN_WRITERS, TABLE_DOMAINS

REGISTER_PATH = Path(__file__).resolve().parent / "DATA_CLASSIFICATION_REGISTER.md"

#: The one informational line the drift test masks — everything else must
#: match the code byte-for-byte.
REGENERATED_PREFIX = "| **Regenerated** |"

_HEADER = """\
# Data Classification Register

**Generated document — never edit by hand.** Rendered from the classification
registry (`classifications.py`), the canonical metadata, and the ownership
registry (`ownership.py`) by `inv classification-register`; the drift gate
(`tests/test_classification_register.py`) fails CI while this file does not
match the code. This is the per-field audit projection of rule 6
the registry stays the source of truth. Vocabulary and handling rules:
`CLASSIFICATIONS.md` and the suite's `Savanna_Data_Classification_Standard.md`.
"""


def _alembic_head() -> str:
    """The migration head this register reflects (schema version stamp)."""
    scripts = ScriptDirectory(str(Path(__file__).resolve().parents[1] / "alembic"))
    return scripts.get_current_head() or "none"


def _table_section(table: Table) -> list[str]:
    classifications = FIELD_CLASSIFICATIONS[table.name]
    lines = [
        f"## `{table.name}`",
        "",
        f"Domain `{TABLE_DOMAINS[table.name]}` · writer"
        f" {DOMAIN_WRITERS[TABLE_DOMAINS[table.name]]}",
        "",
        "| Column | Type | Nullable | Classification |",
        "|---|---|---|---|",
    ]
    lines.extend(
        f"| `{column.name}` | `{column.type}` | {'yes' if column.nullable else 'no'}"
        f" | **{classifications[column.name]}** |"
        for column in table.columns
    )
    lines.append("")
    return lines


def render_register(regenerated_on: str) -> str:
    """The full register as Markdown; ``regenerated_on`` is DD-MM-YYYY."""
    tables = [Base.metadata.tables[name] for name in sorted(Base.metadata.tables)]
    totals = {"clinical": 0, "personal": 0, "internal": 0}
    for table in tables:
        for classification in FIELD_CLASSIFICATIONS[table.name].values():
            totals[classification] += 1

    lines = [
        _HEADER,
        "| | |",
        "|---|---|",
        f"| **Schema version (Alembic head)** | `{_alembic_head()}` |",
        f"| **Tables / columns** | {len(tables)} / {sum(totals.values())} |",
        "| **clinical / personal / internal columns** |"
        f" {totals['clinical']} / {totals['personal']} / {totals['internal']} |",
        f"{REGENERATED_PREFIX} {regenerated_on} |",
        "",
    ]
    for table in tables:
        lines.extend(_table_section(table))
    return "\n".join(lines)


def write_register() -> None:
    """Regenerate ``DATA_CLASSIFICATION_REGISTER.md`` in place (the inv task's body)."""
    REGISTER_PATH.write_text(render_register(date.today().strftime("%d-%m-%Y")))


if __name__ == "__main__":
    write_register()
    print(f"wrote {REGISTER_PATH}")
