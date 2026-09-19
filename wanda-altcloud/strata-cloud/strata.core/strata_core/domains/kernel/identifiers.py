"""The ``kernel.identifiers`` sub-domain: typed external identities.

Moved from the ``device_readings`` domain at Identity Mapping:
the external-identifier tables are canonical identity infrastructure, not
readings vocabulary. ``user_external_id_types`` is a closed catalogue
(reference rows inserted by migration with deterministic ids, like the role
catalogue) and ``user_external_ids`` maps a person to an external identifier
of that type, with temporal validity: at most one live (un-ended) mapping per
(type, value) — superseding ends the previous row (``ended_at``), never
deletes it, so identity history survives for audit. The partial
unique index backstops that invariant under concurrency.

Cognito is one mapped identity among several (``Cognito Sub``); the
legacy platform — the old coach site is itself called Summit — contributes the
``Legacy Summit`` family: the Django user id (always), plus the
internal patient/coach id its own data is keyed on (legacy weight readings key
on the patient one); device identifiers are the SmartMeter types. Writer:
``strata.engine.auth`` — the identity write path, see
``strata_core.ownership``.
"""

from datetime import datetime
from typing import Final

from sqlalchemy import DateTime, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from strata_core.db.base import Base
from strata_core.db.common import TimestampMixin, new_id

#: Canonical identifier-type names — import these, never hard-code the strings.
COGNITO_SUB: Final = "Cognito Sub"
LEGACY_SUMMIT_DJANGO_USER_ID: Final = "Legacy Summit Django User ID"
LEGACY_SUMMIT_PATIENT_ID: Final = "Legacy Summit Patient ID"
LEGACY_SUMMIT_COACH_ID: Final = "Legacy Summit Coach ID"

#: The catalogue's reference rows — the migrations insert exactly these (a
#: closed list: a further type is a requirements change, not a code-level addition).
EXTERNAL_ID_TYPE_NAMES: Final[tuple[str, ...]] = (
    "SmartMeter Scale",
    "SmartMeter Blood Pressure",
    COGNITO_SUB,
    LEGACY_SUMMIT_DJANGO_USER_ID,
    LEGACY_SUMMIT_PATIENT_ID,
    LEGACY_SUMMIT_COACH_ID,
)

#: Deterministic catalogue ids, inserted by migration and identical in every
#: database — so fixtures and consumers never need an id lookup.
#: (Pinned to the migrations by strata.core's round-trip test.)
EXTERNAL_ID_TYPE_IDS: Final[dict[str, str]] = {
    name: "extid-" + name.lower().replace(" ", "-") for name in EXTERNAL_ID_TYPE_NAMES
}


class UserExternalIdType(TimestampMixin, Base):
    __tablename__ = "user_external_id_types"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)


class UserExternalId(TimestampMixin, Base):
    __tablename__ = "user_external_ids"
    __table_args__ = (
        # At most one live (un-ended) mapping per identifier type and value.
        Index(
            "uq_user_external_ids_active",
            "type_id",
            "external_id",
            unique=True,
            postgresql_where=text("ended_at IS NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("user_profiles.id"), nullable=False)
    type_id: Mapped[str] = mapped_column(ForeignKey("user_external_id_types.id"), nullable=False)
    external_id: Mapped[str] = mapped_column(String, nullable=False)
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
