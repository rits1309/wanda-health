"""kernel.identifiers: the Legacy Summit identifier split

Owner-directed catalogue change: the identifier type ``Legacy ID``
(``extid-legacy-id``) becomes ``Legacy Summit Django User ID`` — catalogue ids
are derived from names, so the id changes too — and two further Summit types
are added: ``Legacy Summit Patient ID`` and ``Legacy Summit Coach ID`` (the
legacy platform's own internal keys; its data, e.g. weight readings, is keyed
on them). The FK on ``user_external_ids.type_id`` is NO ACTION, so the rename
is insert-new → repoint mappings (active AND ended — history is never deleted)
→ delete-old.

Downgrade mirrors the rename losslessly, and drops the two added types;
mapping rows of those types are deleted with them — they have no representable
type in the old catalogue. Lossless for the round-trip check on an empty
database, best-effort (dev-only, by design) on data written after upgrade.

Revision ID: a1127b8feee7
Revises: c3a91e40d5b8
Create Date: 2026-07-22 11:07:50.016225

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'a1127b8feee7'
down_revision: Union[str, None] = 'c3a91e40d5b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Names and deterministic ids pinned to EXTERNAL_ID_TYPE_NAMES/IDS
# (strata_core.domains.kernel.identifiers) by the migrations round-trip test.
_OLD = {"id": "extid-legacy-id", "name": "Legacy ID"}
_RENAMED = {
    "id": "extid-legacy-summit-django-user-id",
    "name": "Legacy Summit Django User ID",
}
_ADDED = [
    {"id": "extid-legacy-summit-patient-id", "name": "Legacy Summit Patient ID"},
    {"id": "extid-legacy-summit-coach-id", "name": "Legacy Summit Coach ID"},
]

_TYPES = sa.table(
    "user_external_id_types",
    sa.column("id", sa.String()),
    sa.column("name", sa.String()),
)


def upgrade() -> None:
    # 1. New catalogue rows first — the renamed type must exist before the
    #    mappings repoint (the FK is NO ACTION, non-deferrable).
    op.bulk_insert(_TYPES, [_RENAMED, *_ADDED])
    # 2. Repoint every mapping, active and ended — history rows keep their
    #    ended_at (superseded rows are ended, never deleted). No collision on
    #    the partial unique index: the target type id is fresh, and the values
    #    were already pairwise unique under the old type.
    op.execute(
        "UPDATE user_external_ids"
        " SET type_id = 'extid-legacy-summit-django-user-id', updated_at = now()"
        " WHERE type_id = 'extid-legacy-id'"
    )
    # 3. The old row is now unreferenced — safe to delete under NO ACTION.
    op.execute("DELETE FROM user_external_id_types WHERE id = 'extid-legacy-id'")


def downgrade() -> None:
    # 1. Restore the old catalogue row so the mappings have an FK target.
    op.bulk_insert(_TYPES, [_OLD])
    # 2. Retype the renamed type's mappings back — lossless, history included.
    op.execute(
        "UPDATE user_external_ids"
        " SET type_id = 'extid-legacy-id', updated_at = now()"
        " WHERE type_id = 'extid-legacy-summit-django-user-id'"
    )
    # 3. Rows of the two added types have no pre-migration type — delete them
    #    (the c3a91e40d5b8 precedent; dev-only, by design), then their
    #    catalogue rows and the renamed row.
    op.execute(
        "DELETE FROM user_external_ids WHERE type_id IN"
        " ('extid-legacy-summit-patient-id', 'extid-legacy-summit-coach-id')"
    )
    op.execute(
        "DELETE FROM user_external_id_types WHERE id IN"
        " ('extid-legacy-summit-django-user-id',"
        " 'extid-legacy-summit-patient-id', 'extid-legacy-summit-coach-id')"
    )
