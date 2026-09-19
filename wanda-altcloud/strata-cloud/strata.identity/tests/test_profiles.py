"""The profile half of the identity seam: subject → profile resolution.

Lazy provisioning (``ensure_profile``/``DEFAULT_TIMEZONE``) was removed —
so profiles here are
created directly and mapped through the identifiers seam, the way every real
writer now works.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from strata_core.domains.kernel import COGNITO_SUB, UserProfile

from strata_identity.identifiers import UnmappedSubjectError, adopt
from strata_identity.profiles import ProfileValidationError, get_profile, update_profile
from strata_identity.security import Principal

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


async def _mapped_profile(session: AsyncSession, sub: str, **fields: str) -> UserProfile:
    profile = UserProfile(
        email=fields.get("email", "user@wandahealth.com"),
        first_name=fields.get("first_name", "Lewis"),
        last_name=fields.get("last_name", "Kane"),
        timezone="America/New_York",
    )
    session.add(profile)
    await session.flush()
    await adopt(session, user_id=profile.id, type_name=COGNITO_SUB, external_id=sub)
    await session.commit()
    return profile


def _principal(sub: str) -> Principal:
    return Principal(sub=sub, roles=["Coach"], token="access-tok")


async def _save(session: AsyncSession, sub: str, **overrides: object) -> UserProfile:
    """update_profile with a valid default payload, overriding individual fields."""
    payload: dict[str, object] = {
        "first_name": "Lewis",
        "last_name": "Kane",
        "display_name": None,
        "timezone": "Europe/London",
        "languages": ["en", "es"],
        "preferred_language": "es",
    }
    payload.update(overrides)
    return await update_profile(session, _principal(sub), **payload)  # type: ignore[arg-type]


async def test_get_profile_resolves_an_active_mapping(session: AsyncSession) -> None:
    """get_profile resolves the subject through kernel.identifiers and eager-loads
    the language links (language_codes works without a further query)."""
    created = await _mapped_profile(session, "sub-1")
    found = await get_profile(session, "sub-1")
    assert found is not None
    assert found.id == created.id
    assert found.language_codes == []  # loaded, just empty
    assert found.effective_display_name == "Lewis Kane"  # no override


async def test_get_profile_returns_none_for_unknown_subjects(session: AsyncSession) -> None:
    """get_profile returns None for a subject with no active mapping — the caller
    fails hard (no lazy provisioning)."""
    assert await get_profile(session, "nope") is None


async def test_update_profile_persists_the_save(session: AsyncSession) -> None:
    """update_profile converges every server-held field for the caller's own
    profile, sets the preferred language, and returns it with the
    spoken links reloaded."""
    await _mapped_profile(session, "sub-upd-1")
    saved = await _save(
        session,
        "sub-upd-1",
        first_name="Thomas",
        last_name="Smith",
        display_name="Tom",
        timezone="America/Chicago",
        languages=["en", "es"],
        preferred_language="es",
    )
    await session.commit()
    assert (saved.first_name, saved.last_name) == ("Thomas", "Smith")
    assert saved.display_name == "Tom"
    assert saved.effective_display_name == "Tom"  # override wins
    assert saved.timezone == "America/Chicago"
    assert saved.preferred_language_code == "es"
    assert saved.language_codes == ["en", "es"]  # loaded, catalogue order


async def test_update_profile_converges_spoken_languages(session: AsyncSession) -> None:
    """Dropping a language removes only its link; the set converges to exactly the
    payload (add missing, drop stale)."""
    await _mapped_profile(session, "sub-upd-2")
    await _save(session, "sub-upd-2", languages=["en", "es"], preferred_language="en")
    await session.commit()
    saved = await _save(session, "sub-upd-2", languages=["en"], preferred_language="en")
    await session.commit()
    assert saved.language_codes == ["en"]


async def test_update_profile_blank_override_clears_to_fallback(session: AsyncSession) -> None:
    """A whitespace-only override collapses to no override: the effective name
    falls back to "First Last" rather than rendering blank."""
    await _mapped_profile(session, "sub-upd-3")
    saved = await _save(
        session, "sub-upd-3", first_name="Ada", last_name="Lovelace", display_name="   "
    )
    await session.commit()
    assert saved.display_name is None
    assert saved.effective_display_name == "Ada Lovelace"


async def test_update_profile_rejects_blank_name(session: AsyncSession) -> None:
    """A blank first or last name is refused before any write."""
    await _mapped_profile(session, "sub-upd-4")
    with pytest.raises(ProfileValidationError, match=""):
        await _save(session, "sub-upd-4", first_name="   ")


async def test_update_profile_rejects_no_languages(session: AsyncSession) -> None:
    """At least one spoken language is required."""
    await _mapped_profile(session, "sub-upd-5")
    with pytest.raises(ProfileValidationError, match=""):
        await _save(session, "sub-upd-5", languages=[], preferred_language=None)


async def test_update_profile_rejects_unknown_language(session: AsyncSession) -> None:
    """A code outside the catalogue is refused, not silently written."""
    await _mapped_profile(session, "sub-upd-6")
    with pytest.raises(ProfileValidationError, match=""):
        await _save(session, "sub-upd-6", languages=["en", "fr"], preferred_language="en")


async def test_update_profile_rejects_preferred_not_spoken(session: AsyncSession) -> None:
    """The preferred language must be one of the spoken ones."""
    await _mapped_profile(session, "sub-upd-7")
    with pytest.raises(ProfileValidationError, match=""):
        await _save(session, "sub-upd-7", languages=["en"], preferred_language="es")


async def test_update_profile_rejects_timezone_outside_the_list(session: AsyncSession) -> None:
    """Only a timezone from the closed list is accepted."""
    await _mapped_profile(session, "sub-upd-8")
    with pytest.raises(ProfileValidationError, match=""):
        await _save(session, "sub-upd-8", timezone="Europe/Madrid")


async def test_update_profile_fails_hard_for_unmapped_subject(session: AsyncSession) -> None:
    """A verified subject with no profile is rejected, never provisioned."""
    with pytest.raises(UnmappedSubjectError):
        await _save(session, "sub-not-mapped")
