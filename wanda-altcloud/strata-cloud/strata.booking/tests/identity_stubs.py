"""Test stand-ins for booking's retired identity constructors.

Same call shape as the old ``Coach``/``Member``/``Admin``/``ProgrammeAssignment``
models, built on the kernel: people become ``user_profiles`` rows (``email``
derived from the id; the Cognito subject is a ``kernel.identifiers`` mapping
row since Identity — tests that need one add it explicitly) with their
spoken languages as ``user_languages`` link rows; a member's
preferred language is the person-level ``preferred_language_code`` FK on the
profile; assignments map
booking's ``user_kind`` string onto the catalogue role (``ROLE_IDS`` is filled
by conftest once the canonical migrations have seeded the catalogue).
"""

from typing import Any

from strata_core.domains.kernel import ROLE_BY_KIND, ROLE_IDS, UserLanguage, UserProfile
from strata_core.domains.kernel import ProgrammeAssignment as KernelAssignment


def _profile(
    id: str,
    display_name: str,
    timezone: str,
    languages: list[str],
    preferred_language: str | None = None,
) -> UserProfile:
    #: the kernel's name is first/last (NOT NULL) with display_name an
    # optional override. The stub keeps its historical display-name call shape:
    # it derives the halves (first-space split, the migration's backfill rule)
    # and stores the given name as the override, so every existing test reads
    # back exactly the display name it wrote.
    first, _, rest = display_name.partition(" ")
    profile = UserProfile(
        id=id,
        email=f"{id}@test.local",
        first_name=first,
        last_name=rest or first,
        display_name=display_name,
        timezone=timezone,
        preferred_language_code=preferred_language,
    )
    # save-update cascade fills user_id on flush when the profile is added
    profile.user_languages = [UserLanguage(language_code=code) for code in languages]
    return profile


def Coach(
    *,
    id: str,
    display_name: str = "Coach",
    timezone: str = "UTC",
    languages: list[str] | tuple[str, ...] = ("en",),
) -> UserProfile:
    return _profile(id, display_name, timezone, list(languages))


def Admin(*, id: str, display_name: str = "Admin", timezone: str = "UTC") -> UserProfile:
    return _profile(id, display_name, timezone, [])


def Member(
    *,
    id: str,
    display_name: str = "Member",
    timezone: str = "UTC",
    languages: list[str] | tuple[str, ...] = ("en",),
    preferred_language: str = "en",
) -> UserProfile:
    # Preferred is always among the spoken languages (the service-level rule).
    codes = list(dict.fromkeys([preferred_language, *languages]))
    return _profile(id, display_name, timezone, codes, preferred_language)


def ProgrammeAssignment(
    *, programme_id: str, user_kind: str, user_id: str, id: str | None = None
) -> KernelAssignment:
    kwargs: dict[str, Any] = {
        "programme_id": programme_id,
        "user_id": user_id,
        "role_id": ROLE_IDS[ROLE_BY_KIND[user_kind]],
    }
    if id is not None:
        kwargs["id"] = id
    return KernelAssignment(**kwargs)
