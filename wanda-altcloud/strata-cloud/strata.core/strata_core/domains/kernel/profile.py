"""The unified user profile and its role-specific detail tables.

Ported shape-for-shape from ``strata.identity``:
the profile IS the canonical identity — a person can
exist, be booked, and hold data with no Cognito account and no email
address. The Cognito subject is a typed mapping row in ``kernel.identifiers``,
never a profile column. Role-specific fields live in one-to-one detail tables
keyed to the profile — composition, not inheritance, because a user can hold
multiple roles. No such table exists today (``member_details`` was dropped
— see below); one is (re)introduced only when a role-specific field
first exists.

Reshaped (revision history): spoken
languages moved off the profile's ARRAY column into the ``user_languages``
link (see ``language.py``), and the preferred language became the person-level
``preferred_language_code`` FK here — it applies to any role, so
``member_details`` no longer carried it and was left a bare member-role marker.

Reshaped again (revision history):
``member_details`` was dropped. Role membership is read from
``user_roles`` — the single role read path already used everywhere — so the
marker table added nothing; a member-specific detail table returns only when a
member-only field first exists.

Reshaped again (User Profile): the person's name
became ``first_name``/``last_name`` (NOT NULL — every user has both) with
``display_name``
demoted to an optional presentation override: shown when set, else "First Last". The
migration backfilled the halves by splitting the historical display name on
the first space and kept every existing value as an override, so nothing
rendered differently at the boundary.
"""

from typing import Final

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from strata_core.db.base import Base
from strata_core.db.common import TimestampMixin, new_id
from strata_core.domains.kernel.language import UserLanguage

#: The closed set of timezones a profile may hold:
#: the US zones plus United Kingdom time. Canonical IANA identifiers — the
#: platform stores these, never an offset (IANA carries its own DST rules); the
#: familiar display labels ("US/Eastern", …) are a presentation concern owned by
#: the Summit UI. Deliberately a code constant, not a table (a timezones table
#: was rejected in planning): the set changes with the product, not with data.
PROFILE_TIMEZONES: Final[tuple[str, ...]] = (
    "America/New_York",  # US/Eastern
    "America/Chicago",  # US/Central
    "America/Denver",  # US/Mountain
    "America/Phoenix",  # US/Arizona
    "America/Los_Angeles",  # US/Pacific
    "America/Anchorage",  # US/Alaska
    "Pacific/Honolulu",  # US/Hawaii
    "Europe/London",  # UK (London)
)

#: The creation default for controlled origins (import, onboarding, seed — there
#: is no lazy provisioning): no new row is ever outside the closed
#: list.
DEFAULT_PROFILE_TIMEZONE: Final[str] = "America/New_York"


class UserProfile(TimestampMixin, Base):
    __tablename__ = "user_profiles"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    # Nullable: a Member may have no email at all. Deliberately NOT unique
    # either: Cognito may reuse an email after an account is
    # deleted and recreated. Uniqueness among login-capable users is an import/
    # onboarding invariant, enforced there — never DDL.
    email: Mapped[str | None] = mapped_column(String, nullable=True)
    # Every user has a first and last name: NOT NULL by construction.
    # Non-blankness is the wire-level rule (binds at Save), never DDL.
    first_name: Mapped[str] = mapped_column(String, nullable=False)
    last_name: Mapped[str] = mapped_column(String, nullable=False)
    # Optional presentation override: shown when set, else "First Last".
    display_name: Mapped[str | None] = mapped_column(String, nullable=True)
    timezone: Mapped[str] = mapped_column(String, nullable=False)  # IANA, e.g. Europe/London
    # At most one preferred language by construction; "preferred is a spoken
    # language" is enforced at the service level, never DDL.
    preferred_language_code: Mapped[str | None] = mapped_column(
        ForeignKey("languages.code"), nullable=True
    )

    # Roles are read through the identity role helpers (or token claims) —
    # a single read path, no ORM relationship to drift against it.
    # Ordered by code — reproduces the historical array order for every existing
    # dataset. Read through an eager load (selectinload) in async consumers.
    user_languages: Mapped[list[UserLanguage]] = relationship(order_by=UserLanguage.language_code)

    @property
    def language_codes(self) -> list[str]:
        """The spoken-language codes, in catalogue order (requires the
        ``user_languages`` relationship to be loaded)."""
        return [ul.language_code for ul in self.user_languages]

    @property
    def effective_display_name(self) -> str:
        """What to show for this person: the override when set, else
        "First Last". The chain ends here — never at the email (first/last
        are NOT NULL, so the fallback always exists)."""
        return self.display_name or f"{self.first_name} {self.last_name}"
