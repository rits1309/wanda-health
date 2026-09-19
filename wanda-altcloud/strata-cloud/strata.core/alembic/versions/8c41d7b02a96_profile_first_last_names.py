"""profile first/last names

The person's name becomes
``first_name``/``last_name`` — NOT NULL, every user has both — and ``display_name`` is demoted to a nullable presentation
override.

Data-preserving in both directions: the upgrade backfills the halves by
splitting the historical display name on the FIRST space (single-token names
copy the token into both fields as a placeholder until the user edits)
and keeps every existing display name as an override, so nothing renders
differently at the boundary. The downgrade refills a NULL display name from
"First Last" (the fallback — identical rendering again) before
restoring NOT NULL and dropping the halves.

Revision ID: 8c41d7b02a96
Revises: 5cd17e752ff3
Create Date: 2026-07-22 21:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '8c41d7b02a96'
down_revision: Union[str, None] = '5cd17e752ff3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('user_profiles', sa.Column('first_name', sa.String(), nullable=True))
    op.add_column('user_profiles', sa.Column('last_name', sa.String(), nullable=True))

    # Backfill: split on the FIRST space — "Sarah Jane Smith" → "Sarah" +
    # "Jane Smith"; a single token ("Cher") copies into both halves.
    # btrim guards against doubled spaces; display_name was NOT NULL until now,
    # so every row backfills and the NOT NULL flip below cannot fail.
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "update user_profiles set"
            " first_name = split_part(btrim(display_name), ' ', 1),"
            " last_name = case"
            "   when strpos(btrim(display_name), ' ') > 0"
            "   then btrim(substr(btrim(display_name), strpos(btrim(display_name), ' ') + 1))"
            "   else btrim(display_name)"
            " end"
        )
    )

    op.alter_column('user_profiles', 'first_name', nullable=False)
    op.alter_column('user_profiles', 'last_name', nullable=False)
    # Existing display names are KEPT as overrides: rendering is
    # identical before and after; only new rows may carry NULL here.
    op.alter_column('user_profiles', 'display_name', nullable=True)


def downgrade() -> None:
    conn = op.get_bind()
    # Refill the fallback wherever no override exists, then restore the
    # historical NOT NULL — identical rendering in this direction too.
    conn.execute(
        sa.text(
            "update user_profiles set display_name = first_name || ' ' || last_name"
            " where display_name is null"
        )
    )
    op.alter_column('user_profiles', 'display_name', nullable=False)
    op.drop_column('user_profiles', 'last_name')
    op.drop_column('user_profiles', 'first_name')
