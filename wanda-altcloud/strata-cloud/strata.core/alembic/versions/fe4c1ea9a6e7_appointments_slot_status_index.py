"""appointments slot status index

: the calendar's appointment join widened from
confirmed-only to ``confirmed | no_show | completed``. Terminal rows sit
outside the partial unique index ``uq_appointments_slot_confirmed`` (filtered
to ``status = 'confirmed'``) and are never deleted, so the join needs plain
``(slot_id, status)`` coverage to stay off sequential scans as history grows.

(The autogenerate diff also proposed dropping a stray non-canonical
``conflict_test`` table found in the local dev database — a leftover manual
experiment, not part of the canonical schema; removed from this revision.)

Revision ID: fe4c1ea9a6e7
Revises: d63d5b060509
Create Date: 2026-07-31 10:02:49.176529

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'fe4c1ea9a6e7'
down_revision: Union[str, None] = 'd63d5b060509'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        'ix_appointments_slot_status', 'appointments', ['slot_id', 'status'], unique=False
    )


def downgrade() -> None:
    op.drop_index('ix_appointments_slot_status', table_name='appointments')
