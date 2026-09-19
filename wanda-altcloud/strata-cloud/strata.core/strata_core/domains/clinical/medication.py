"""Member medications — FDA product_id natural-key links."""

from datetime import date

from sqlalchemy import Date, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from strata_core.db.base import Base
from strata_core.db.common import TimestampMixin, new_id


class MemberMedication(TimestampMixin, Base):
    __tablename__ = "member_medications"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    member_user_id: Mapped[str] = mapped_column(ForeignKey("user_profiles.id"), nullable=False)
    # FDA product_id natural key (e.g. 0169-4130_4cd6f447-…) — the legacy
    # medication_id IS this format, so re-keying at import is direct.
    product_id: Mapped[str] = mapped_column(String, nullable=False)
    # Legacy free-text prescription note.
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # null starts_on at import (not in the legacy shape); null ends_on =
    # active prescription.
    starts_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    ends_on: Mapped[date | None] = mapped_column(Date, nullable=True)
