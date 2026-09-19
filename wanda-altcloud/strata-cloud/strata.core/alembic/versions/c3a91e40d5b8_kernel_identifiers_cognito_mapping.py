"""kernel.identifiers: Cognito becomes a typed mapping (Identity)

The single-revision move (delivery plan, owner-resolved 20-07-2026
nothing is deployed, only dev data exists, and the owner has authorised
reshaping it): the identifier-type catalogue gains ``Cognito Sub`` and
``Legacy ID``; every ``user_profiles.cognito_sub`` value backfills into an
active mapping row; the column (and its unique index) is dropped; and
``email`` becomes nullable (— a person may have no email address).

Downgrade restores the previous shape structurally: the column is rebuilt
from the active mappings, rows without one get a ``downgraded-<id>``
placeholder to satisfy NOT NULL + unique, and NULL emails become empty
strings — lossless for the round-trip check on an empty database, best-effort
(dev-only, by design) on seeded data.

Revision ID: c3a91e40d5b8
Revises: aedf37dd40b7
Create Date: 2026-07-20
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3a91e40d5b8"
down_revision: Union[str, None] = "aedf37dd40b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Deterministic catalogue rows — names and ids must match
# strata_core.domains.kernel.identifiers EXTERNAL_ID_TYPE_NAMES/EXTERNAL_ID_TYPE_IDS
# (asserted by the round-trip test). A closed list.
_NEW_TYPES = [
    {"id": "extid-cognito-sub", "name": "Cognito Sub"},
    {"id": "extid-legacy-id", "name": "Legacy ID"},
]


def upgrade() -> None:
    types = sa.table(
        "user_external_id_types", sa.column("id", sa.String()), sa.column("name", sa.String())
    )
    op.bulk_insert(types, _NEW_TYPES)
    # Backfill: one ACTIVE Cognito Sub mapping per existing profile (shape).
    op.execute(
        """
        INSERT INTO user_external_ids (id, user_id, type_id, external_id, registered_at)
        SELECT replace(gen_random_uuid()::text, '-', ''), id, 'extid-cognito-sub',
               cognito_sub, now()
        FROM user_profiles
        """
    )
    op.drop_index(op.f("ix_user_profiles_cognito_sub"), table_name="user_profiles")
    op.drop_column("user_profiles", "cognito_sub")
    op.alter_column("user_profiles", "email", existing_type=sa.String(), nullable=True)


def downgrade() -> None:
    op.execute("UPDATE user_profiles SET email = '' WHERE email IS NULL")
    op.alter_column("user_profiles", "email", existing_type=sa.String(), nullable=False)
    op.add_column("user_profiles", sa.Column("cognito_sub", sa.String(), nullable=True))
    op.execute(
        """
        UPDATE user_profiles p
        SET cognito_sub = m.external_id
        FROM user_external_ids m
        WHERE m.user_id = p.id AND m.type_id = 'extid-cognito-sub' AND m.ended_at IS NULL
        """
    )
    op.execute(
        "UPDATE user_profiles SET cognito_sub = 'downgraded-' || id WHERE cognito_sub IS NULL"
    )
    op.alter_column("user_profiles", "cognito_sub", existing_type=sa.String(), nullable=False)
    op.create_index(
        op.f("ix_user_profiles_cognito_sub"), "user_profiles", ["cognito_sub"], unique=True
    )
    op.execute(
        "DELETE FROM user_external_ids WHERE type_id IN ('extid-cognito-sub', 'extid-legacy-id')"
    )
    op.execute(
        "DELETE FROM user_external_id_types WHERE id IN ('extid-cognito-sub', 'extid-legacy-id')"
    )
