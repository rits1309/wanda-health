"""The identifiers seam: resolve, adopt (supersede-for-audit), end, principal resolution.

The seam behaviours every consumer relies on: idempotent re-adopt,
end-not-delete supersede, exclusive (one-per-person) vs non-exclusive (device)
types, and the ONE principal-resolution rule — mapping-first, profile-id
fallback, fail hard. The active-mapping partial unique index (the race
backstop) is covered by strata-core's own suite.
"""

import logging
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from strata_core.domains.kernel import (
    COGNITO_SUB,
    LEGACY_SUMMIT_DJANGO_USER_ID,
    UserExternalId,
    UserProfile,
)

from strata_identity.identifiers import (
    UnknownIdentifierTypeError,
    UnmappedSubjectError,
    active_mappings_for_user,
    adopt,
    end_mapping,
    resolve_principal,
    resolve_profile,
)
from strata_identity.security import Principal

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

T0 = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)


async def _person(session: AsyncSession, id: str, email: str | None = None) -> UserProfile:
    profile = UserProfile(
        id=id,
        email=email,
        first_name="Person",
        last_name=id,
        timezone="Etc/UTC",
    )
    session.add(profile)
    await session.flush()
    return profile


async def test_adopt_then_resolve_round_trips(session: AsyncSession) -> None:
    """Adopting an identifier then resolving it returns the same profile."""
    await _person(session, "idf-1")
    await adopt(session, user_id="idf-1", type_name=COGNITO_SUB, external_id="sub-idf-1")
    found = await resolve_profile(session, type_name=COGNITO_SUB, external_id="sub-idf-1")
    assert found is not None and found.id == "idf-1"


async def test_resolve_ignores_an_ended_mapping(session: AsyncSession) -> None:
    """An ended mapping is invisible to resolution - only the active row counts."""
    await _person(session, "idf-2")
    await adopt(session, user_id="idf-2", type_name=COGNITO_SUB, external_id="sub-idf-2")
    await end_mapping(session, type_name=COGNITO_SUB, external_id="sub-idf-2")
    assert await resolve_profile(session, type_name=COGNITO_SUB, external_id="sub-idf-2") is None


async def test_re_adopt_by_the_same_user_is_idempotent(session: AsyncSession) -> None:
    """Re-adopting by the same user returns the existing row - no audit flip-flop."""
    await _person(session, "idf-3")
    first = await adopt(
        session, user_id="idf-3", type_name=LEGACY_SUMMIT_DJANGO_USER_ID, external_id="8713267"
    )
    second = await adopt(
        session, user_id="idf-3", type_name=LEGACY_SUMMIT_DJANGO_USER_ID, external_id="8713267"
    )
    assert second.id == first.id  # the existing row, unchanged — no audit flip-flop


async def test_adopt_supersedes_another_holder_ending_not_deleting(
    session: AsyncSession,
) -> None:
    """Adopting a held identifier ends (never deletes) the prior holder's row."""
    await _person(session, "idf-4a")
    await _person(session, "idf-4b")
    original = await adopt(
        session,
        user_id="idf-4a",
        type_name=LEGACY_SUMMIT_DJANGO_USER_ID,
        external_id="4400",
        registered_at=T0,
    )
    await adopt(
        session, user_id="idf-4b", type_name=LEGACY_SUMMIT_DJANGO_USER_ID, external_id="4400"
    )
    assert original.ended_at is not None  # ended, not deleted
    resolved = await resolve_profile(
        session, type_name=LEGACY_SUMMIT_DJANGO_USER_ID, external_id="4400"
    )
    assert resolved is not None and resolved.id == "idf-4b"
    history = (
        (await session.execute(select(UserExternalId).where(UserExternalId.external_id == "4400")))
        .scalars()
        .all()
    )
    assert len(history) == 2  # both rows survive for audit


async def test_exclusive_adopt_ends_the_users_previous_mapping(session: AsyncSession) -> None:
    """The pool-recreation / placeholder-supersede shape: one active Cognito Sub
    per person — adopting a NEW subject ends the old one in the same act."""
    await _person(session, "idf-5")
    await adopt(session, user_id="idf-5", type_name=COGNITO_SUB, external_id="old-sub")
    await adopt(
        session, user_id="idf-5", type_name=COGNITO_SUB, external_id="new-sub", exclusive=True
    )
    assert await resolve_profile(session, type_name=COGNITO_SUB, external_id="old-sub") is None
    resolved = await resolve_profile(session, type_name=COGNITO_SUB, external_id="new-sub")
    assert resolved is not None and resolved.id == "idf-5"


async def test_non_exclusive_adopt_keeps_sibling_mappings(session: AsyncSession) -> None:
    """Device semantics: a member may hold several devices of one type — a second
    registration must not end the first (Strata.Connect delegates here at)."""
    await _person(session, "idf-6")
    first = await adopt(
        session, user_id="idf-6", type_name="SmartMeter Scale", external_id="SM-0001"
    )
    await adopt(session, user_id="idf-6", type_name="SmartMeter Scale", external_id="SM-0002")
    assert first.ended_at is None  # the sibling device stays actively registered


async def test_active_mappings_for_user_sees_only_live_rows_of_the_type(
    session: AsyncSession,
) -> None:
    """The migration gate's read: ended rows and other types are invisible."""
    await _person(session, "idf-8")
    assert await active_mappings_for_user(session, user_id="idf-8", type_name=COGNITO_SUB) == []
    await adopt(
        session, user_id="idf-8", type_name=LEGACY_SUMMIT_DJANGO_USER_ID, external_id="9100"
    )
    await adopt(session, user_id="idf-8", type_name=COGNITO_SUB, external_id="sub-idf-8a")
    await end_mapping(session, type_name=COGNITO_SUB, external_id="sub-idf-8a")
    await adopt(session, user_id="idf-8", type_name=COGNITO_SUB, external_id="sub-idf-8b")
    live = await active_mappings_for_user(session, user_id="idf-8", type_name=COGNITO_SUB)
    assert [m.external_id for m in live] == ["sub-idf-8b"]


async def test_end_mapping_is_idempotent(session: AsyncSession) -> None:
    """Ending a mapping twice is safe - the second call returns None."""
    await _person(session, "idf-7")
    await adopt(session, user_id="idf-7", type_name=COGNITO_SUB, external_id="sub-idf-7")
    ended = await end_mapping(session, type_name=COGNITO_SUB, external_id="sub-idf-7")
    assert ended is not None and ended.ended_at is not None
    assert await end_mapping(session, type_name=COGNITO_SUB, external_id="sub-idf-7") is None


async def test_unknown_type_is_a_closed_list_error(session: AsyncSession) -> None:
    """An unknown identifier type raises UnknownIdentifierTypeError - a closed catalogue."""
    with pytest.raises(UnknownIdentifierTypeError):
        await resolve_profile(session, type_name="NHS Number", external_id="x")


async def test_principal_resolves_mapping_first(session: AsyncSession) -> None:
    """A principal resolves through its active Cognito Sub mapping before any fallback."""
    await _person(session, "idf-8")
    await adopt(session, user_id="idf-8", type_name=COGNITO_SUB, external_id="sub-idf-8")
    profile = await resolve_principal(session, Principal(sub="sub-idf-8", roles=[], token=""))
    assert profile.id == "idf-8"


async def test_principal_falls_back_to_the_profile_id(session: AsyncSession) -> None:
    """Dev tokens are historically minted with sub = the profile id."""
    await _person(session, "idf-9")
    profile = await resolve_principal(session, Principal(sub="idf-9", roles=[], token=""))
    assert profile.id == "idf-9"


async def test_principal_with_neither_fails_hard(session: AsyncSession) -> None:
    """/ no implicit provisioning — an unresolvable subject is
    rejected, and nothing is created."""
    with pytest.raises(UnmappedSubjectError):
        await resolve_principal(session, Principal(sub="no-such-subject", roles=[], token=""))
    assert (
        await resolve_profile(session, type_name=COGNITO_SUB, external_id="no-such-subject")
    ) is None


async def test_rejection_never_carries_the_subject_value(
    session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    """the fail-hard rejection's log line and exception message carry no raw
    subject value — a Cognito Sub is a catalogued identifier and stays out of logs."""
    sentinel = "sub-sentinel-XR77120"
    with (
        caplog.at_level(logging.WARNING, logger="strata_identity.identifiers"),
        pytest.raises(UnmappedSubjectError) as excinfo,
    ):
        await resolve_principal(session, Principal(sub=sentinel, roles=[], token=""))
    assert sentinel not in str(excinfo.value)
    assert all(sentinel not in record.getMessage() for record in caplog.records)
