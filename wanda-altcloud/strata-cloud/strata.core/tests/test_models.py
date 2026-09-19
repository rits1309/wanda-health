"""Model round-trips against a migrated ephemeral Postgres.

The database is built by the canonical migrations (never ``create_all``), so
these also prove the baseline migration produces a schema the models can use:
the kernel's constraints hold (multi-role uniqueness, real FKs on programme
assignments — the constraint booking's local triple could never have).
"""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from strata_core.domains.clinical import (
    MemberDemographics,
    MemberDiagnosis,
    MemberMedication,
    MemberProcedure,
)
from strata_core.domains.kernel import (
    LANGUAGE_CODES,
    ROLE_IDS,
    ROLE_NAMES,
    Language,
    Programme,
    ProgrammeAssignment,
    Role,
    UserLanguage,
    UserProfile,
    UserRole,
)
from strata_core.domains.reference import Diagnosis

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


async def _profile(session: AsyncSession, sub: str = "sub-1") -> UserProfile:
    profile = UserProfile(
        email=f"{sub}@example.test",
        first_name="Sarah",
        last_name="Mitchell",
        timezone="Europe/London",
        preferred_language_code="es",
    )
    session.add(profile)
    await session.flush()
    session.add(UserLanguage(user_id=profile.id, language_code="en"))
    session.add(UserLanguage(user_id=profile.id, language_code="es"))
    await session.commit()
    return profile


async def test_profile_role_round_trip(session: AsyncSession) -> None:
    """A profile with a role round-trips through the kernel models (membership is
    read from user_roles; member_details was dropped)."""
    profile = await _profile(session)
    coach = (await session.execute(select(Role).where(Role.name == "Coach"))).scalar_one()
    session.add(UserRole(user_id=profile.id, role_id=coach.id))
    await session.commit()

    names = (
        (await session.execute(select(Role.name).join(UserRole, UserRole.role_id == Role.id)))
        .scalars()
        .all()
    )
    assert names == ["Coach"]

    catalogue = (await session.execute(select(Role.name))).scalars().all()
    assert sorted(catalogue) == sorted(ROLE_NAMES)


async def test_duplicate_role_assignment_is_rejected(session: AsyncSession) -> None:
    """Assigning the same role twice violates the unique constraint (IntegrityError)."""
    profile = await _profile(session, sub="sub-2")
    admin = (await session.execute(select(Role).where(Role.name == "Admin"))).scalar_one()
    session.add(UserRole(user_id=profile.id, role_id=admin.id))
    await session.commit()
    session.add(UserRole(user_id=profile.id, role_id=admin.id))
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_programme_assignment_enforces_real_fks(session: AsyncSession) -> None:
    """Programme assignments insert only against real profile/programme/role rows (FKs enforced)."""
    profile = await _profile(session, sub="sub-3")
    member = (await session.execute(select(Role).where(Role.name == "Member"))).scalar_one()
    programme = Programme(name="Hypertension coaching")
    session.add(programme)
    await session.commit()

    session.add(
        ProgrammeAssignment(programme_id=programme.id, user_id=profile.id, role_id=member.id)
    )
    await session.commit()

    # The whole point of the reshape: a dangling person id can no longer be stored.
    session.add(
        ProgrammeAssignment(programme_id=programme.id, user_id="not-a-person", role_id=member.id)
    )
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_reference_tables_serve_trgm_backed_search(session: AsyncSession) -> None:
    """Reference tables answer ILIKE substring search, the trgm-indexed access path."""
    session.add(
        Diagnosis(
            code="G932",
            short_title="Benign IH",
            long_title="Benign intracranial hypertension",
            order_number=1,
        )
    )
    await session.commit()
    query = select(Diagnosis).where(Diagnosis.long_title.ilike("%intracranial%"))
    found = (await session.execute(query)).scalars().all()
    assert [d.code for d in found] == ["G932"]


async def test_role_catalogue_ids_are_deterministic(session: AsyncSession) -> None:
    """The migration's catalogue rows carry the kernel's fixed ids — in every database."""
    rows = (await session.execute(select(Role.id, Role.name))).tuples().all()
    assert {name: role_id for role_id, name in rows} == ROLE_IDS


async def test_language_catalogue_codes_are_deterministic(session: AsyncSession) -> None:
    """The migration seeds the language catalogue exactly as the kernel declares it."""
    codes = (await session.execute(select(Language.code))).scalars().all()
    assert sorted(codes) == sorted(LANGUAGE_CODES)


async def test_duplicate_user_language_is_rejected(session: AsyncSession) -> None:
    """A duplicate (user, language) spoken-language link is rejected by the unique index."""
    profile = await _profile(session, sub="sub-lang-dup")
    session.add(UserLanguage(user_id=profile.id, language_code="en"))
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_preferred_language_must_exist_in_the_catalogue(session: AsyncSession) -> None:
    """The FK is the whole point of the reshape: a free-string language can no
    longer be stored — neither as spoken nor as preferred."""
    profile = UserProfile(
        email="sub-bad-lang@example.test",
        first_name="Sarah",
        last_name="Mitchell",
        timezone="Europe/London",
        preferred_language_code="tlh",  # not in the catalogue
    )
    session.add(profile)
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_clinical_profile_round_trip(session: AsyncSession) -> None:
    """The clinical domain: one row per table against a real
    member; terminology linkage is a bare natural key — no cross-domain FK to
    satisfy, so the codes here need no reference rows."""
    profile = await _profile(session, sub="sub-clinical")
    session.add(MemberDiagnosis(member_user_id=profile.id, code="I10", starts_on=date(2024, 3, 12)))
    session.add(
        MemberProcedure(
            member_user_id=profile.id,
            code="0H5RXZZ",
            starts_on=date(2023, 5, 4),
            ends_on=date(2023, 5, 4),
            status="completed",
        )
    )
    session.add(
        MemberMedication(
            member_user_id=profile.id,
            product_id="0143-1240_a7d33628-8d4e-4de1-9a67-72830a125a62",
            note="10mg once daily",
            starts_on=date(2024, 3, 12),
        )
    )
    session.add(
        MemberDemographics(
            member_user_id=profile.id,
            sex="F",
            date_of_birth=date(1988, 7, 22),
            height_in=Decimal("65.0"),
        )
    )
    await session.commit()

    diagnosis = (
        await session.execute(
            select(MemberDiagnosis).where(MemberDiagnosis.member_user_id == profile.id)
        )
    ).scalar_one()
    assert diagnosis.code == "I10"
    assert diagnosis.ends_on is None  # null ends_on = ongoing
    demographics = await session.get(MemberDemographics, profile.id)
    assert demographics is not None
    assert demographics.height_in == Decimal("65.0")  # inches


async def test_clinical_rows_require_a_real_member(session: AsyncSession) -> None:
    """The member FK is real DDL — unlike the terminology link."""
    session.add(MemberDiagnosis(member_user_id="no-such-profile", code="I10"))
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_profile_requires_first_and_last_name(session: AsyncSession) -> None:
    """A profile cannot be stored without both name halves — NOT NULL by
    construction."""
    session.add(UserProfile(email="no-last@example.test", first_name="Cher", timezone="Etc/UTC"))
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_same_code_recurrence_is_multiple_rows(session: AsyncSession) -> None:
    """deliberately NO uniqueness on (member, code) — a recurring condition
    is one row per period. If this test starts failing on a constraint, someone
    added DDL the design forbids."""
    profile = await _profile(session, sub="sub-recurrence")
    session.add(
        MemberDiagnosis(
            member_user_id=profile.id,
            code="G43909",
            starts_on=date(2022, 1, 10),
            ends_on=date(2022, 9, 30),
        )
    )
    session.add(
        MemberDiagnosis(member_user_id=profile.id, code="G43909", starts_on=date(2025, 11, 2))
    )
    await session.commit()

    rows = (
        (
            await session.execute(
                select(MemberDiagnosis)
                .where(MemberDiagnosis.member_user_id == profile.id)
                .order_by(MemberDiagnosis.starts_on)
            )
        )
        .scalars()
        .all()
    )
    assert [r.ends_on for r in rows] == [date(2022, 9, 30), None]  # historic, then current


async def test_demographics_is_one_to_one(session: AsyncSession) -> None:
    """member_user_id is the PK — a second demographics row for the same member is
    structurally impossible."""
    profile = await _profile(session, sub="sub-demographics-dup")
    session.add(MemberDemographics(member_user_id=profile.id, sex="F"))
    await session.commit()
    with pytest.raises(IntegrityError):
        # A fresh INSERT against the same PK (session.add of a second instance
        # with the same key would be an identity-map error, not a DB check).
        await session.execute(insert(MemberDemographics).values(member_user_id=profile.id, sex="M"))


async def test_display_name_is_an_optional_override(session: AsyncSession) -> None:
    """display_name stores NULL (no override) and the effective name falls back
    to "First Last" — never further."""
    profile = UserProfile(
        email="no-override@example.test",
        first_name="Sarah",
        last_name="Mitchell",
        timezone="Etc/UTC",
    )
    session.add(profile)
    await session.commit()
    assert profile.display_name is None
    assert profile.effective_display_name == "Sarah Mitchell"
    profile.display_name = "Sav"
    assert profile.effective_display_name == "Sav"
