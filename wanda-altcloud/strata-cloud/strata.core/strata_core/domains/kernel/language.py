"""The language catalogue and user-to-language assignment.

Normalised from the Identity Phase 1 shapes (``user_profiles.languages``
ARRAY + free-string ``member_details.preferred_language``) under the
data-migration epic — the kernel reshape is recorded in 's revision
history. Languages are data with referential integrity, not free strings:
``languages`` is a closed catalogue keyed by the lowercase ISO 639-1 code
itself (the code IS the deterministic id — the ``roles`` /
``user_external_id_types`` pattern without a synthetic key), and
``user_languages`` assigns them many-to-many. The preferred language is the
person-level ``user_profiles.preferred_language_code`` FK — at most one by
construction; "preferred is a spoken language" is a service-level rule, not
DDL. Spoken-language lists are read ordered by code, which reproduces the
historical array order for every existing dataset.
"""

from typing import Final

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from strata_core.db.base import Base
from strata_core.db.common import TimestampMixin, new_id

#: The catalogue's reference rows — lowercase ISO 639-1 codes, inserted by the
#: domain migration and identical in every database (pinned by the round-trip test).
LANGUAGE_CODES: Final[tuple[str, ...]] = ("en", "es")


class Language(TimestampMixin, Base):
    __tablename__ = "languages"

    code: Mapped[str] = mapped_column(String, primary_key=True)  # e.g. "en"


class UserLanguage(TimestampMixin, Base):
    __tablename__ = "user_languages"
    __table_args__ = (UniqueConstraint("user_id", "language_code"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("user_profiles.id"), nullable=False)
    language_code: Mapped[str] = mapped_column(ForeignKey("languages.code"), nullable=False)
