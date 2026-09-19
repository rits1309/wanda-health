"""Member procedures — ICD-10-PCS natural-key links."""

from datetime import date

from sqlalchemy import Date, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from strata_core.db.base import Base
from strata_core.db.common import TimestampMixin, new_id


class MemberProcedure(TimestampMixin, Base):
    __tablename__ = "member_procedures"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    member_user_id: Mapped[str] = mapped_column(ForeignKey("user_profiles.id"), nullable=False)
    # ICD-10-PCS natural key (e.g. 0H5RXZZ) — service-level validation.
    code: Mapped[str] = mapped_column(String, nullable=False)
    # a same-day procedure carries ends_on = starts_on or null.
    starts_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    ends_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Legacy status carried as-is; vocabulary and its mapping onto the period
    # model bind at the B3 import.
    status: Mapped[str | None] = mapped_column(String, nullable=True)
