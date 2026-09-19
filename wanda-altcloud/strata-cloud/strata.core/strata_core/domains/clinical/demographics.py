"""Member demographics — one-to-one clinical profile facts.

Deliberately in the clinical domain, not the kernel: these are health-record
facts owned by the future member-profile service. All value columns
are nullable and permissive — the legacy vocabularies (sex codes, ethnicity,
single-letter marital status) bind at the B2/B3 import.

Keyed on ``member_user_id``: a foreign key to ``user_profiles.id``
is named ``<relationship>_user_id`` — the ``_user_id`` suffix marks the value a
user-profile id, the ``member_`` prefix the relationship this user holds in the row.
"""

from datetime import date
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from strata_core.db.base import Base
from strata_core.db.common import TimestampMixin


class MemberDemographics(TimestampMixin, Base):
    __tablename__ = "member_demographics"

    member_user_id: Mapped[str] = mapped_column(ForeignKey("user_profiles.id"), primary_key=True)
    sex: Mapped[str | None] = mapped_column(String, nullable=True)
    date_of_birth: Mapped[date | None] = mapped_column(Date, nullable=True)
    ethnicity: Mapped[str | None] = mapped_column(String, nullable=True)
    marital_status: Mapped[str | None] = mapped_column(String, nullable=True)
    # Stored in inches (owner 17-07-2026): matches the legacy US data
    # as-is; convert at the presentation layer if the product needs metric.
    height_in: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
