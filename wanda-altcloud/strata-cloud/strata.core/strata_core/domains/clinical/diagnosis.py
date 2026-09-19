"""Member diagnoses — ICD-10-CM natural-key links."""

from datetime import date

from sqlalchemy import Date, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from strata_core.db.base import Base
from strata_core.db.common import TimestampMixin, new_id


class MemberDiagnosis(TimestampMixin, Base):
    __tablename__ = "member_diagnoses"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    member_user_id: Mapped[str] = mapped_column(ForeignKey("user_profiles.id"), nullable=False)
    # ICD-10-CM natural key (e.g. G43C0) — validated against the reference
    # catalogue at the service level, deliberately no cross-domain FK.
    code: Mapped[str] = mapped_column(String, nullable=False)
    # null starts_on = unknown start (treated as current); null ends_on =
    # ongoing. No uniqueness — recurrence is multiple rows for the same code.
    starts_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    ends_on: Mapped[date | None] = mapped_column(Date, nullable=True)
