"""The seed's invariants: the canonical cast, the comparator, and subject adoption.

Since the cast lives in ``strata_core.fixtures.cast`` and the database
half of seeding is the canonical ``demo`` profile; this script's own logic is
the Cognito half plus adopting real pool subjects onto the canonical profiles.
The cast/comparator tests are pure (no AWS, no DB); the adoption tests run on
the ephemeral testcontainers Postgres (marked ``integration``). The live AWS
run is the walkthrough's job.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from strata_core.domains.kernel import (
    COGNITO_SUB,
    EXTERNAL_ID_TYPE_IDS,
    UserExternalId,
    UserProfile,
)
from strata_core.fixtures import load_profile
from strata_core.fixtures.cast import DEMO_CAST
from strata_identity.roles import ROLE_ADMIN, ROLE_COACH, ROLE_NAMES, roles_for

from scripts.seed import _adopt_subject, reconciliation_mismatches


async def _active_sub(session: AsyncSession, user_id: str) -> str:
    """The user's single ACTIVE Cognito Sub mapping value (kernel.identifiers)."""
    return (
        await session.execute(
            select(UserExternalId.external_id).where(
                UserExternalId.user_id == user_id,
                UserExternalId.type_id == EXTERNAL_ID_TYPE_IDS[COGNITO_SUB],
                UserExternalId.ended_at.is_(None),
            )
        )
    ).scalar_one()


def test_cast_identities_are_unique() -> None:
    """The demo cast's emails and usernames are unique."""
    emails = [u.email for u in DEMO_CAST]
    usernames = [u.username for u in DEMO_CAST]
    assert len(set(emails)) == len(emails)
    assert len(set(usernames)) == len(usernames)


def test_cast_usernames_are_never_email_format() -> None:
    """The pool signs in by username with email as an ALIAS, and
    Cognito rejects email-format usernames on an alias pool — the seed creates
    users by cast username, so this is the invariant no AWS-less run can catch."""
    for user in DEMO_CAST:
        assert "@" not in user.username, f"{user.username!r} would be rejected by an alias pool"


def test_cast_roles_are_catalogue_names() -> None:
    """Every cast member carries at least one role, all from the role catalogue."""
    for user in DEMO_CAST:
        assert user.roles, f"{user.email} has no roles"
        assert set(user.roles) <= set(ROLE_NAMES)


def test_a_preferred_language_is_always_among_the_spoken_languages() -> None:
    """Preferred language is person-level since (any role may carry one);
    the invariant that remains is the service-level rule: a preferred language is
    always one the person actually speaks."""
    for user in DEMO_CAST:
        if user.preferred_language is not None:
            assert user.preferred_language in user.languages


def test_exactly_one_multi_role_user_proves_coach_plus_admin() -> None:
    """Exactly one cast member is Coach + Admin, the multi-role proof case."""
    multi = [u for u in DEMO_CAST if set(u.roles) >= {ROLE_COACH, ROLE_ADMIN}]
    assert len(multi) == 1


def test_reconciliation_passes_when_groups_mirror_roles() -> None:
    """Reconciliation reports nothing when Cognito groups mirror the stored roles."""
    groups = {"a@x.com": {"coach", "admin"}, "b@x.com": {"member"}}
    roles = {"a@x.com": {"Coach", "Admin"}, "b@x.com": {"Member"}}
    assert reconciliation_mismatches(groups, roles) == []


def test_reconciliation_ignores_unknown_groups() -> None:
    """Unknown (non-role) Cognito groups are ignored by reconciliation."""
    groups = {"a@x.com": {"coach", "some-sso-group"}}
    roles = {"a@x.com": {"Coach"}}
    assert reconciliation_mismatches(groups, roles) == []


def test_reconciliation_reports_drift_in_both_directions() -> None:
    """Reconciliation reports both missing and surplus role/group drift."""
    groups = {"a@x.com": {"coach"}, "b@x.com": set()}
    roles = {"a@x.com": {"Coach", "Admin"}, "b@x.com": {"Member"}}
    problems = reconciliation_mismatches(groups, roles)
    assert len(problems) == 2
    assert any("a@x.com" in p for p in problems)
    assert any("b@x.com" in p for p in problems)


# --- subject adoption against a real (ephemeral) database --------------------------

DANA = "seed-dana@wandahealth.com"


@pytest.mark.integration
@pytest.mark.anyio
async def test_adoption_updates_the_placeholder_and_survives_pool_recreation(
    db_engine: AsyncEngine, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """Seed adoption rebinds the placeholder profile to the pool sub, surviving recreation."""
    await load_profile(db_engine, "demo")  # the database half, as the seed runs it

    async with db_sessionmaker() as session:  # first real seed: placeholder → pool sub
        profile_id = await _adopt_subject(session, "pool-sub-1", DANA)
        assert await roles_for(session, profile_id) == ["Admin", "Coach"]  # from the profile

    async with db_sessionmaker() as session:  # the pool was recreated: same email, new sub
        await _adopt_subject(session, "pool-sub-2", DANA)
        count = (
            await session.execute(
                select(func.count()).select_from(UserProfile).where(UserProfile.email == DANA)
            )
        ).scalar_one()
        profile = (
            await session.execute(select(UserProfile).where(UserProfile.email == DANA))
        ).scalar_one()
        assert count == 1  # adopted, not duplicated
        assert await _active_sub(session, profile.id) == "pool-sub-2"
        ended = (
            (
                await session.execute(
                    select(UserExternalId.external_id).where(
                        UserExternalId.user_id == profile.id,
                        UserExternalId.type_id == EXTERNAL_ID_TYPE_IDS[COGNITO_SUB],
                        UserExternalId.ended_at.is_not(None),
                    )
                )
            )
            .scalars()
            .all()
        )
    # History survives: the placeholder and the first pool subject are ENDED,
    # never deleted.
    assert set(ended) == {"seed-sub-dana", "pool-sub-1"}


@pytest.mark.integration
@pytest.mark.anyio
async def test_adoption_requires_the_profile_to_exist(
    db_engine: AsyncEngine, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """A missing canonical profile is a loud failure — the demo profile must load first."""
    async with db_sessionmaker() as session:
        with pytest.raises(RuntimeError, match="load the demo fixture profile"):
            await _adopt_subject(session, "any-sub", "nobody@wandahealth.com")


@pytest.mark.integration
@pytest.mark.anyio
async def test_adoption_survives_a_duplicate_email(
    db_engine: AsyncEngine, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """Emails are non-unique by design: a /me-provisioned duplicate must not crash the
    seed (the old MultipleResultsFound), and a row already holding the pool subject wins
    (never trips the active-mapping unique index, uq_user_external_ids_active)."""
    await load_profile(db_engine, "demo")
    async with db_sessionmaker() as session:  # the duplicate a real sign-in creates
        duplicate = UserProfile(
            email=DANA,
            first_name="Dana",
            last_name="Reyes",
            timezone="America/Chicago",
        )
        session.add(duplicate)
        await session.flush()
        session.add(
            UserExternalId(
                user_id=duplicate.id,
                type_id=EXTERNAL_ID_TYPE_IDS[COGNITO_SUB],
                external_id="pool-sub-dana",
                registered_at=datetime.now(UTC),
            )
        )
        await session.commit()

    async with db_sessionmaker() as session:
        adopted = await _adopt_subject(session, "pool-sub-dana", DANA)  # must not raise
        assert await _active_sub(session, adopted) == "pool-sub-dana"  # the sub-holder won
