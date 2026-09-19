"""The role catalogue and user-to-role assignment.

Ported shape-for-shape from ``strata.identity``: roles
are data, not table structure — ``roles`` holds the human-readable catalogue
(reference rows inserted by the canonical baseline migration) and
``user_roles`` assigns them many-to-many, so one user can hold several roles
at once. ``user_roles`` is the authoritative assignment source; Cognito group
membership mirrors it (the assign/revoke logic that writes both lives in the
``strata.identity`` seam library).
"""

from typing import Final

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from strata_core.db.base import Base
from strata_core.db.common import TimestampMixin, new_id

#: The catalogue's reference rows — the baseline migration inserts exactly these.
ROLE_NAMES: Final[tuple[str, ...]] = ("Coach", "Member", "Admin")

#: Deterministic catalogue ids, inserted by the baseline migration and identical in
#: every database — so predicates, fixtures and consumers never need an id lookup.
#: (Pinned to the migration by strata.core's round-trip test.)
ROLE_IDS: Final[dict[str, str]] = {name: f"role-{name.lower()}" for name in ROLE_NAMES}

#: The lowercase vocabulary (booking's historical ``user_kind`` strings; Cognito's
#: group names) → catalogue name, derived so it can never drift from ROLE_NAMES.
ROLE_BY_KIND: Final[dict[str, str]] = {name.lower(): name for name in ROLE_NAMES}


class Role(TimestampMixin, Base):
    __tablename__ = "roles"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)  # e.g. "Coach"


class UserRole(TimestampMixin, Base):
    __tablename__ = "user_roles"
    __table_args__ = (UniqueConstraint("user_id", "role_id"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("user_profiles.id"), nullable=False)
    role_id: Mapped[str] = mapped_column(ForeignKey("roles.id"), nullable=False)
