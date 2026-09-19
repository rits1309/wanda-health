"""Programmes and who is in them.

``programmes`` ports booking's Phase 1 model unchanged: programme-level
configuration lives on the programme row.

``programme_assignments`` is the one deliberate reshape in the kernel: booking's
local ``(programme_id, user_kind, user_id)`` triple could carry **no** foreign
key (``user_id`` pointed at three different tables). With people in one table
the row becomes fully constrained — ``user_id`` references the profile and
``role_id`` says which hat the person wears in the programme (a coach-and-admin
can hold both memberships). The port maps booking's ``user_kind`` strings onto
``role_id``. Admin reach over coaches stays *derived* from shared programme
membership — there is no direct admin-to-coach assignment.
"""

from sqlalchemy import Boolean, ColumnElement, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from strata_core.db.base import Base
from strata_core.db.common import TimestampMixin, new_id
from strata_core.domains.kernel.role import ROLE_IDS, ROLE_NAMES


class Programme(TimestampMixin, Base):
    __tablename__ = "programmes"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String, nullable=False)
    suggestion_window_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=4)
    reassignment_notification_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    no_show_grace_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=15)
    reminder_lead_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=24)
    slot_horizon_weeks: Mapped[int] = mapped_column(Integer, nullable=False, default=4)
    reschedule_link_ttl_days: Mapped[int] = mapped_column(Integer, nullable=False, default=7)


class ProgrammeAssignment(TimestampMixin, Base):
    __tablename__ = "programme_assignments"
    __table_args__ = (UniqueConstraint("programme_id", "user_id", "role_id"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    programme_id: Mapped[str] = mapped_column(ForeignKey("programmes.id"), nullable=False)
    user_id: Mapped[str] = mapped_column(ForeignKey("user_profiles.id"), nullable=False)
    role_id: Mapped[str] = mapped_column(ForeignKey("roles.id"), nullable=False)

    @classmethod
    def assigned_as(cls, role_name: str) -> ColumnElement[bool]:
        """Filter assignments by the catalogue role — the drop-in replacement for
        booking's old ``user_kind == "coach"`` predicate. Compares against the
        deterministic catalogue id, and validates the name — a typo'd or renamed role
        raises here instead of silently matching nothing."""
        if role_name not in ROLE_IDS:
            raise ValueError(f"Unknown role {role_name!r}; expected one of {ROLE_NAMES}")
        return cls.role_id == ROLE_IDS[role_name]
