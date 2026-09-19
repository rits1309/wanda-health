"""normalise user languages

 (revision history): languages become a closed
catalogue keyed by the lowercase ISO 639-1 code (the roles /
user_external_id_types pattern — the code IS the deterministic id), spoken
languages move from the ``user_profiles.languages`` ARRAY into the
``user_languages`` link, and the preferred language becomes the person-level
``user_profiles.preferred_language_code`` FK (moved off ``member_details``,
which remains as the member-role marker).

Data-preserving in both directions: the upgrade seeds ``en``/``es`` plus any
code already present in the data (no row is lost to the closed catalogue),
migrates every array entry to a link row, and lifts the member preferred
language onto the profile. The downgrade rebuilds the array (ordered by code —
the historical order of every existing dataset) and refills
``member_details.preferred_language`` (profile FK, else first spoken, else
``en`` — the read-path fallback consumers used).

Revision ID: 5cd17e752ff3
Revises: a1127b8feee7
Create Date: 2026-07-16 16:33:58.503273

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '5cd17e752ff3'
down_revision: Union[str, None] = 'a1127b8feee7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('languages',
    sa.Column('code', sa.String(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('code', name=op.f('pk_languages'))
    )
    op.create_table('user_languages',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('user_id', sa.String(), nullable=False),
    sa.Column('language_code', sa.String(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['language_code'], ['languages.code'], name=op.f('fk_user_languages_language_code_languages')),
    sa.ForeignKeyConstraint(['user_id'], ['user_profiles.id'], name=op.f('fk_user_languages_user_id_user_profiles')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_user_languages')),
    sa.UniqueConstraint('user_id', 'language_code', name=op.f('uq_user_languages_user_id_language_code'))
    )

    # The catalogue rows must match strata_core.domains.kernel.LANGUAGE_CODES
    # (asserted by the round-trip test) — plus any code already present in the
    # data, so the closed catalogue never loses an existing row to an FK error.
    conn = op.get_bind()
    found = set(
        conn.execute(
            sa.text(
                "select distinct lower(code) from ("
                " select unnest(languages) as code from user_profiles"
                " union select preferred_language from member_details) t"
                " where code is not null"
            )
        ).scalars()
    )
    languages = sa.table('languages', sa.column('code', sa.String()))
    op.bulk_insert(languages, [{'code': c} for c in sorted({'en', 'es'} | found)])

    op.add_column('user_profiles', sa.Column('preferred_language_code', sa.String(), nullable=True))
    op.create_foreign_key(op.f('fk_user_profiles_preferred_language_code_languages'), 'user_profiles', 'languages', ['preferred_language_code'], ['code'])

    # Spoken arrays → link rows; the member preferred language → the person-level FK.
    conn.execute(
        sa.text(
            "insert into user_languages (id, user_id, language_code)"
            " select replace(gen_random_uuid()::text, '-', ''), p.id, lower(l.code)"
            " from user_profiles p cross join lateral unnest(p.languages) as l(code)"
            " on conflict on constraint uq_user_languages_user_id_language_code do nothing"
        )
    )
    conn.execute(
        sa.text(
            "update user_profiles p set preferred_language_code = lower(md.preferred_language)"
            " from member_details md where md.user_id = p.id"
        )
    )

    op.drop_column('user_profiles', 'languages')
    op.drop_column('member_details', 'preferred_language')


def downgrade() -> None:
    conn = op.get_bind()

    op.add_column('user_profiles', sa.Column('languages', postgresql.ARRAY(sa.VARCHAR()), autoincrement=False, nullable=True))
    conn.execute(
        sa.text(
            "update user_profiles p set languages = coalesce("
            " (select array_agg(ul.language_code order by ul.language_code)"
            "  from user_languages ul where ul.user_id = p.id), '{}')"
        )
    )
    op.alter_column('user_profiles', 'languages', nullable=False)

    op.add_column('member_details', sa.Column('preferred_language', sa.VARCHAR(), autoincrement=False, nullable=True))
    conn.execute(
        sa.text(
            "update member_details md set preferred_language = coalesce("
            " p.preferred_language_code,"
            " (select min(ul.language_code) from user_languages ul where ul.user_id = md.user_id),"
            " 'en')"
            " from user_profiles p where p.id = md.user_id"
        )
    )
    op.alter_column('member_details', 'preferred_language', nullable=False)

    op.drop_constraint(op.f('fk_user_profiles_preferred_language_code_languages'), 'user_profiles', type_='foreignkey')
    op.drop_column('user_profiles', 'preferred_language_code')
    op.drop_table('user_languages')
    op.drop_table('languages')
