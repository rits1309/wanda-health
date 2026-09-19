"""Cancellation policies: programme default, optional coach override.

Policies are immutable after creation — a change creates a new version and deactivates the old
one. The effective policy for an appointment is the coach's active policy if one
exists, else the programme's.

Ported shape-for-shape from ``strata.booking``, except that the
people columns (``coach_id``/``member_id``) now reference the kernel's
``user_profiles`` — the local coach/member/admin stand-ins are gone;
role-ness is enforced by service-level guards, never DDL.
"""

from sqlalchemy import Boolean, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from strata_core.db.base import Base
from strata_core.db.common import TimestampMixin, new_id


class CancellationPolicy(TimestampMixin, Base):
    __tablename__ = "cancellation_policies"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    scope: Mapped[str] = mapped_column(String, nullable=False)  # programme | coach
    programme_id: Mapped[str] = mapped_column(ForeignKey("programmes.id"), nullable=False)
    coach_id: Mapped[str | None] = mapped_column(ForeignKey("user_profiles.id"), nullable=True)
    cancellation_window_hours: Mapped[int] = mapped_column(Integer, nullable=False)
    cancellation_allowed_by: Mapped[str] = mapped_column(
        String, nullable=False
    )  # member|coach|both
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
