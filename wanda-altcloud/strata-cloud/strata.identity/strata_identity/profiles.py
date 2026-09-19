"""Profile read and write — the profile half of the identity seam.

Lives beside :mod:`strata_identity.identifiers` (the mapping half)
and :mod:`strata_identity.roles` (the role half): session-taking functions the
consumer calls with its own ``AsyncSession``; the models live in
``strata-core``. The Cognito subject is a typed ``kernel.identifiers`` mapping
— resolution goes through the identifiers seam, one profile per active
subject. Lazy provisioning (``ensure_profile`` and its ``DEFAULT_TIMEZONE``
policy) was removed (an unmapped subject fails hard).

The write path (``update_profile``) is the profile page's Save:
it resolves the caller's own profile through
the same ONE resolution rule as ``/me`` (``resolve_principal`` — real-pool and
dev tokens alike), enforces the profile-write invariants,
and converges the fields. It is the shared write path, so it validates its own
inputs rather than trusting the caller.
"""

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from strata_core.db.common import new_id
from strata_core.domains.kernel import (
    COGNITO_SUB,
    PROFILE_TIMEZONES,
    Language,
    UserLanguage,
    UserProfile,
)

from strata_identity.identifiers import resolve_principal, resolve_profile
from strata_identity.security import Principal


class ProfileValidationError(ValueError):
    """A profile-write input broke a service-level invariant (2/3/9).

    Distinct from the wire layer's type validation: these are the cross-field
    and domain rules the seam owns so every writer enforces them identically.
    Consumers map it to 422.
    """


async def get_profile(session: AsyncSession, cognito_sub: str) -> UserProfile | None:
    """The profile holding an ACTIVE ``Cognito Sub`` mapping for ``cognito_sub``,
    with its spoken-language links eager-loaded (``language_codes`` is derived
    from them)."""
    profile = await resolve_profile(session, type_name=COGNITO_SUB, external_id=cognito_sub)
    if profile is None:
        return None
    return await _with_languages(session, profile.id)


async def update_profile(
    session: AsyncSession,
    principal: Principal,
    *,
    first_name: str,
    last_name: str,
    display_name: str | None,
    timezone: str,
    languages: list[str],
    preferred_language: str | None,
) -> UserProfile:
    """Persist the profile page's Save for the caller's own profile.

    Resolves through the same rule as ``/me`` (``resolve_principal``) so a
    verified subject with no profile raises ``UnmappedSubjectError`` — the write
    never provisions. Enforces the invariants the profile page also gates
    at Save: non-blank names, at least one spoken language, the
    preferred language among the spoken ones, and a timezone from the
    closed list. Flushes; the caller owns the commit. Returns the profile
    with its spoken-language links reloaded for the response.
    """
    first, last = first_name.strip(), last_name.strip()
    if not first or not last:
        raise ProfileValidationError("first_name and last_name must be non-blank ")
    if not languages:
        raise ProfileValidationError("at least one spoken language is required ")
    known = set((await session.execute(select(Language.code))).scalars().all())
    unknown = sorted({code for code in languages if code not in known})
    if unknown:
        raise ProfileValidationError(f"unknown language codes {unknown} ")
    if preferred_language is not None and preferred_language not in languages:
        raise ProfileValidationError("preferred language must be one of the spoken languages ")
    if timezone not in PROFILE_TIMEZONES:
        raise ProfileValidationError(f"timezone {timezone!r} is not in the offered list ")

    profile = await resolve_principal(session, principal)
    profile.first_name = first
    profile.last_name = last
    # Blank override collapses to "no override": the effective name falls back to
    # "First Last" rather than rendering an empty string.
    profile.display_name = (display_name or "").strip() or None
    profile.timezone = timezone
    profile.preferred_language_code = preferred_language
    await _converge_languages(session, profile.id, languages)
    await session.flush()
    return await _with_languages(session, profile.id)


async def _converge_languages(session: AsyncSession, user_id: str, codes: list[str]) -> None:
    """Converge ``user_languages`` for ``user_id`` to exactly ``codes`` — add
    missing, drop stale. Mirrors the fixtures' convergence so seed and runtime
    writes leave the same shape."""
    rows = (
        (await session.execute(select(UserLanguage).where(UserLanguage.user_id == user_id)))
        .scalars()
        .all()
    )
    current = {row.language_code: row for row in rows}
    for code in codes:
        if code not in current:
            await session.execute(
                pg_insert(UserLanguage)
                .values(id=new_id(), user_id=user_id, language_code=code)
                .on_conflict_do_nothing(index_elements=["user_id", "language_code"])
            )
    for code, row in current.items():
        if code not in codes:
            await session.delete(row)


async def _with_languages(session: AsyncSession, user_id: str) -> UserProfile:
    """Reload a profile with its spoken-language links eager-loaded."""
    result = await session.execute(
        select(UserProfile)
        .options(selectinload(UserProfile.user_languages))
        .where(UserProfile.id == user_id)
    )
    return result.scalar_one()
