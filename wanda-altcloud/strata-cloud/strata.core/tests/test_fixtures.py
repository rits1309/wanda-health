"""Fixture profiles: deterministic, idempotent, loadable inside testcontainers.

Loading ``demo`` converges the migrated database to the dev cast exactly; a
second load changes nothing; a stale role assignment is revoked (the converge
semantics the auth seed relies on from).
"""

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from sqlalchemy.orm import selectinload

from strata_core.domains.kernel import (
    COGNITO_SUB,
    EXTERNAL_ID_TYPE_IDS,
    Role,
    UserExternalId,
    UserLanguage,
    UserProfile,
    UserRole,
)
from strata_core.fixtures import build_diagnosis, build_product, load_profile
from strata_core.fixtures.cast import DEMO_CAST
from strata_core.fixtures.factories import assign_role, converge_roles


def test_unknown_profile_is_rejected() -> None:
    """An unknown fixture profile name raises ValueError, never silently seeding nothing."""
    import asyncio

    with pytest.raises(ValueError, match="unknown profile"):
        asyncio.run(load_profile(None, "nope"))  # type: ignore[arg-type]


def test_reference_builders_have_sensible_defaults() -> None:
    """Reference builders honour overrides and default everything else sensibly."""
    product = build_product(brand_name="Coagulex")
    assert product.brand_name == "Coagulex"
    assert product.raw == {}
    assert build_diagnosis().code == "G932"


@pytest.mark.integration
@pytest.mark.anyio
async def test_demo_profile_converges_and_is_idempotent(engine: AsyncEngine) -> None:
    """The demo profile seeds the 9-person cast with roles, idempotently."""
    report = await load_profile(engine, "demo")
    assert report.people == len(DEMO_CAST) == 9

    async with async_sessionmaker(engine)() as session:
        # Membership is read from user_roles (member_details dropped,):
        # exactly the five members hold the Member role (incl. post-migrated Marta).
        members = (
            await session.execute(
                select(func.count(UserRole.id))
                .join(Role, UserRole.role_id == Role.id)
                .where(Role.name == "Member")
            )
        ).scalar_one()
        assert members == 5  # the members

        dana_roles = (
            (
                await session.execute(
                    select(Role.name)
                    .join(UserRole, UserRole.role_id == Role.id)
                    .join(UserProfile, UserProfile.id == UserRole.user_id)
                    .where(UserProfile.email == "seed-dana@wandahealth.com")
                )
            )
            .scalars()
            .all()
        )
        assert sorted(dana_roles) == ["Admin", "Coach"]  # the multi-role proof

        sam = (
            await session.execute(
                select(UserProfile)
                .options(selectinload(UserProfile.user_languages))
                .where(UserProfile.email == "seed-sam@wandahealth.com")
            )
        ).scalar_one()
        assert sam.language_codes == ["en", "es"]  # converged link rows, catalogue order
        assert sam.preferred_language_code == "es"  # person-level preferred

        # The cast mixes display-name overrides: Sam and Dana carry
        # one, Casey does not — both rendering paths live in seeded data.
        assert (sam.first_name, sam.last_name, sam.display_name) == ("Sam", "Carter", "Sam")
        assert sam.effective_display_name == "Sam"
        casey = (
            await session.execute(
                select(UserProfile).where(UserProfile.email == "seed-casey@wandahealth.com")
            )
        ).scalar_one()
        assert (casey.first_name, casey.last_name, casey.display_name) == ("Casey", "Ellis", None)
        assert casey.effective_display_name == "Casey Ellis"
    # Idempotent: a second load converges to the same state.
    report = await load_profile(engine, "demo")
    assert report.people == 9
    async with async_sessionmaker(engine)() as session:
        assignments = (await session.execute(select(func.count(UserRole.id)))).scalar_one()
        assert assignments == 10  # 9 people, Dana twice
        links = (await session.execute(select(func.count(UserLanguage.id)))).scalar_one()
        assert links == 12  # 9 people; Dana, Sam and Marta speak two


@pytest.mark.integration
@pytest.mark.anyio
async def test_converge_revokes_a_stale_role(engine: AsyncEngine) -> None:
    """Re-running the demo profile revokes a role that drifted onto a cast member."""
    await load_profile(engine, "demo")
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        casey = (
            await session.execute(
                select(UserProfile).where(UserProfile.email == "seed-casey@wandahealth.com")
            )
        ).scalar_one()
        await assign_role(session, casey, "Admin")  # drift the state
        await session.commit()

    await load_profile(engine, "demo")  # converge back
    async with async_sessionmaker(engine)() as session:
        names = (
            (
                await session.execute(
                    select(Role.name)
                    .join(UserRole, UserRole.role_id == Role.id)
                    .where(UserRole.user_id == casey.id)
                )
            )
            .scalars()
            .all()
        )
        assert names == ["Coach"]


@pytest.mark.integration
@pytest.mark.anyio
async def test_minimal_profile_loads(engine: AsyncEngine) -> None:
    """The minimal profile loads and seeds no people."""
    assert (await load_profile(engine, "minimal")).people == 0


@pytest.mark.integration
@pytest.mark.anyio
async def test_booking_acceptance_profile_loads_the_walkthrough_dataset(
    engine: AsyncEngine,
) -> None:
    """The booking-acceptance profile seeds the whole walkthrough dataset (10 people + booking)."""
    from strata_core.domains.booking import Appointment, AvailabilityPattern, CancellationPolicy
    from strata_core.domains.kernel import ROLE_IDS, Programme, ProgrammeAssignment

    report = await load_profile(engine, "booking-acceptance")
    assert report.people == 10  # the 9-person demo cast (incl. m-6) + the booking-only extra (m-3)

    async def count(model: type) -> int:
        async with async_sessionmaker(engine)() as session:
            return (await session.execute(select(func.count()).select_from(model))).scalar_one()

    assert await count(Programme) == 2
    # 12 seeded + the migrated stand-in m-6 converged into p-1
    assert await count(ProgrammeAssignment) == 13  # incl. Dana's scoped p-1 admin
    assert await count(CancellationPolicy) == 3
    assert await count(AvailabilityPattern) == 3
    assert await count(Appointment) == 3

    # The fixture's own post-migrated stand-in (m-6, carrying a Legacy Django ID)
    # is converged into p-1 from this clean seed — /with no real import.
    async with async_sessionmaker(engine)() as session:
        m6 = (
            await session.execute(
                select(ProgrammeAssignment.programme_id, ProgrammeAssignment.role_id).where(
                    ProgrammeAssignment.user_id == "m-6"
                )
            )
        ).all()
        # Post-migrated shape: the mappings an import leaves behind.
        m6_rows = (
            await session.execute(
                select(UserExternalId.type_id, UserExternalId.external_id).where(
                    UserExternalId.user_id == "m-6", UserExternalId.ended_at.is_(None)
                )
            )
        ).all()
        m6_external = {type_id: external_id for type_id, external_id in m6_rows}
    assert [(pid, rid) for pid, rid in m6] == [("p-1", ROLE_IDS["Member"])]
    from strata_core.domains.kernel import LEGACY_SUMMIT_DJANGO_USER_ID, LEGACY_SUMMIT_PATIENT_ID

    assert m6_external[EXTERNAL_ID_TYPE_IDS[LEGACY_SUMMIT_DJANGO_USER_ID]] == "9000001"
    assert m6_external[EXTERNAL_ID_TYPE_IDS[LEGACY_SUMMIT_PATIENT_ID]] == "9100001"

    # Idempotent: converging again changes nothing.
    assert (await load_profile(engine, "booking-acceptance")).people == 10
    assert await count(ProgrammeAssignment) == 13


@pytest.mark.integration
@pytest.mark.anyio
async def test_booking_acceptance_converges_migrated_member_assignments(
    engine: AsyncEngine,
) -> None:
    """A Legacy-Summit-mapped member gains the p-1 assignment, convergent on rerun."""
    from datetime import UTC, datetime

    from strata_core.domains.kernel import (
        LEGACY_SUMMIT_DJANGO_USER_ID,
        ROLE_IDS,
        ProgrammeAssignment,
    )
    from strata_core.fixtures.factories import create_profile, register_device

    await load_profile(engine, "booking-acceptance")

    # Stage three imported-style profiles the way scripts/import_user.py leaves them:
    # generated-id profile + Member role (membership = user_roles since),
    # a legacy mapping, NO assignment.
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        registered_at = datetime(2026, 7, 1, tzinfo=UTC)
        migrated = await create_profile(
            session,
            email=None,
            first_name="Theo",
            last_name="VH",  # names are first/last since; imports set no override
            profile_id="mig-theo",
            preferred_language="en",  # the import script always sets it, so the fixture does too
        )
        await assign_role(session, migrated, "Member")
        await register_device(
            session,
            user_id=migrated.id,
            type_name=LEGACY_SUMMIT_DJANGO_USER_ID,
            external_id="2253",
            registered_at=registered_at,
        )
        # Negative guards: a member WITHOUT the mapping, and a mapped COACH,
        # must both stay unassigned.
        unmapped = await create_profile(
            session, email=None, first_name="No", last_name="Mapping", profile_id="mig-plain"
        )
        await assign_role(session, unmapped, "Member")
        mapped_coach = await create_profile(
            session, email="mig-coach@test.local", first_name="Imported", last_name="Coach"
        )
        await assign_role(session, mapped_coach, "Coach")
        await register_device(
            session,
            user_id=mapped_coach.id,
            type_name=LEGACY_SUMMIT_DJANGO_USER_ID,
            external_id="7001",
            registered_at=registered_at,
        )
        coach_id = mapped_coach.id
        await session.commit()

    await load_profile(engine, "booking-acceptance")  # converges the assignment

    async def theo_rows() -> list[tuple[str, str, str, object]]:
        async with async_sessionmaker(engine)() as session:
            rows = (
                (
                    await session.execute(
                        select(ProgrammeAssignment).where(ProgrammeAssignment.user_id == "mig-theo")
                    )
                )
                .scalars()
                .all()
            )
            return [(r.id, r.programme_id, r.role_id, r.created_at) for r in rows]

    converged = await theo_rows()
    assert [(r[0], r[1], r[2]) for r in converged] == [
        ("pa-p-1-member-mig-theo", "p-1", ROLE_IDS["Member"])
    ]

    await load_profile(engine, "booking-acceptance")  # rerun: no duplicate, no change
    # created_at pins that the row was left alone, not deleted and recreated
    # under the same deterministic id.
    assert await theo_rows() == converged

    async with async_sessionmaker(engine)() as session:
        untouched = (
            await session.execute(
                select(func.count())
                .select_from(ProgrammeAssignment)
                .where(ProgrammeAssignment.user_id.in_(["mig-plain", coach_id]))
            )
        ).scalar_one()
        assert untouched == 0

    # The kind→role mapping holds: p-1 has exactly 3 coach assignments, and its
    # admins are all-programmes Alex plus SCOPED Dana — who must NOT administer
    # p-2 (the scoped-admin proof).
    async with async_sessionmaker(engine)() as session:
        coach_assignments = (
            await session.execute(
                select(func.count())
                .select_from(ProgrammeAssignment)
                .where(
                    ProgrammeAssignment.programme_id == "p-1",
                    ProgrammeAssignment.assigned_as("Coach"),
                )
            )
        ).scalar_one()
        p1_admins = (
            await session.execute(
                select(ProgrammeAssignment.user_id).where(
                    ProgrammeAssignment.programme_id == "p-1",
                    ProgrammeAssignment.assigned_as("Admin"),
                )
            )
        ).scalars()
        p2_admins = (
            await session.execute(
                select(ProgrammeAssignment.user_id).where(
                    ProgrammeAssignment.programme_id == "p-2",
                    ProgrammeAssignment.assigned_as("Admin"),
                )
            )
        ).scalars()
    assert coach_assignments == 3
    assert sorted(p1_admins) == ["a-1", "c-2"]
    assert sorted(p2_admins) == ["a-1"]


@pytest.mark.integration
@pytest.mark.anyio
async def test_example_slots_land_on_weekdays_and_reproduce_under_a_pinned_clock(
    engine: AsyncEngine,
) -> None:
    """Slot placement derives from ``now``: strictly future, weekdays only (the
    Friday-seed bug put everything on Saturday, invisible to Summit's weekday
    calendar), and the same pinned clock converges to the same rows."""
    from datetime import UTC, datetime, timedelta

    from strata_core.domains.booking import Slot

    pinned = datetime(2026, 7, 10, 15, 0, tzinfo=UTC)  # a Friday — the old bug's trigger
    await load_profile(engine, "booking-acceptance", now=pinned)

    async def slot_starts() -> list[datetime]:
        async with async_sessionmaker(engine)() as session:
            return list((await session.execute(select(Slot.start_utc).order_by(Slot.id))).scalars())

    starts = await slot_starts()
    assert len(starts) == 6
    assert all(start.weekday() < 5 for start in starts)  # Mon–Fri only
    assert all(pinned < start <= pinned + timedelta(days=8) for start in starts)  # future only

    # Same pinned clock → identical placement (determinism anchors on the clock).
    await load_profile(engine, "booking-acceptance", now=pinned)
    assert await slot_starts() == starts

    # A different reseed instant reshuffles the placement.
    await load_profile(engine, "booking-acceptance", now=datetime(2026, 7, 8, 9, 30, tzinfo=UTC))
    assert await slot_starts() != starts


@pytest.mark.integration
@pytest.mark.anyio
async def test_readings_demo_profile_registers_the_demo_devices(engine: AsyncEngine) -> None:
    """The placeholder device id attributes under BOTH catalogue types to
    Morgan (the runbook posts both example payloads verbatim); the rest of the
    member cast gets distinct-id devices."""
    from strata_core.domains.kernel import EXTERNAL_ID_TYPE_IDS, UserExternalId
    from strata_core.fixtures.readings_data import DEVICE_REGISTRATIONS, EXAMPLE_DEVICE_ID

    report = await load_profile(engine, "readings-demo")
    assert report.people == 9  # the demo cast, no booking extras

    async with async_sessionmaker(engine)() as session:
        morgan_id = (
            await session.execute(
                select(UserProfile.id).where(UserProfile.email == "seed-morgan@wandahealth.com")
            )
        ).scalar_one()
        for type_name in ("SmartMeter Scale", "SmartMeter Blood Pressure"):
            owner = (
                await session.execute(
                    select(UserExternalId.user_id).where(
                        UserExternalId.type_id == EXTERNAL_ID_TYPE_IDS[type_name],
                        UserExternalId.external_id == EXAMPLE_DEVICE_ID,
                        UserExternalId.ended_at.is_(None),
                    )
                )
            ).scalar_one()
            assert owner == morgan_id

    # Idempotent: a second load adds nothing. Count DEVICE mappings only — the
    # cast's Cognito Sub mappings share the table since Identity.
    await load_profile(engine, "readings-demo")
    device_type_ids = [
        EXTERNAL_ID_TYPE_IDS["SmartMeter Scale"],
        EXTERNAL_ID_TYPE_IDS["SmartMeter Blood Pressure"],
    ]
    async with async_sessionmaker(engine)() as session:
        rows = (
            await session.execute(
                select(func.count(UserExternalId.id)).where(
                    UserExternalId.type_id.in_(device_type_ids)
                )
            )
        ).scalar_one()
    assert rows == len(DEVICE_REGISTRATIONS) == 5


@pytest.mark.integration
@pytest.mark.anyio
async def test_readings_demo_supersedes_a_drifted_registration(engine: AsyncEngine) -> None:
    """A device registered away from its cast owner converges back on reload with
    supersede-for-audit semantics — history rows are ended, never deleted."""
    from datetime import UTC, datetime

    from strata_core.domains.kernel import EXTERNAL_ID_TYPE_IDS, UserExternalId
    from strata_core.fixtures.factories import register_device
    from strata_core.fixtures.readings_data import EXAMPLE_DEVICE_ID

    await load_profile(engine, "readings-demo")
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        priya_id = (
            await session.execute(
                select(UserProfile.id).where(UserProfile.email == "seed-priya@wandahealth.com")
            )
        ).scalar_one()
        await register_device(  # drift: Morgan's scale moves to Priya
            session,
            user_id=priya_id,
            type_name="SmartMeter Scale",
            external_id=EXAMPLE_DEVICE_ID,
            registered_at=datetime(2026, 6, 15, tzinfo=UTC),
        )
        await session.commit()

    await load_profile(engine, "readings-demo")  # converge back
    async with async_sessionmaker(engine)() as session:
        morgan_id = (
            await session.execute(
                select(UserProfile.id).where(UserProfile.email == "seed-morgan@wandahealth.com")
            )
        ).scalar_one()
        scale_rows = (
            (
                await session.execute(
                    select(UserExternalId).where(
                        UserExternalId.type_id == EXTERNAL_ID_TYPE_IDS["SmartMeter Scale"],
                        UserExternalId.external_id == EXAMPLE_DEVICE_ID,
                    )
                )
            )
            .scalars()
            .all()
        )
    active = [row for row in scale_rows if row.ended_at is None]
    assert [row.user_id for row in active] == [morgan_id]
    assert len(scale_rows) == 3  # original + drift + re-converge, all kept for audit


@pytest.mark.integration
@pytest.mark.anyio
async def test_converge_roles_direct(engine: AsyncEngine) -> None:
    """converge_roles grants and revokes until the stored role set matches the target exactly."""
    await load_profile(engine, "demo")
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        sam = (
            await session.execute(
                select(UserProfile).where(UserProfile.email == "seed-sam@wandahealth.com")
            )
        ).scalar_one()
        await converge_roles(session, sam, ("Member", "Coach"))
        await session.commit()
        names = (
            (
                await session.execute(
                    select(Role.name)
                    .join(UserRole, UserRole.role_id == Role.id)
                    .where(UserRole.user_id == sam.id)
                )
            )
            .scalars()
            .all()
        )
        assert sorted(names) == ["Coach", "Member"]


@pytest.mark.integration
@pytest.mark.anyio
async def test_duplicate_emails_converge_instead_of_crashing(engine: AsyncEngine) -> None:
    """Emails are deliberately non-unique (Cognito pool recreation): a /me-provisioned
    duplicate must not brick the loader with MultipleResultsFound — it converges the
    earliest row."""
    await load_profile(engine, "demo")
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        session.add(
            UserProfile(
                email="seed-dana@wandahealth.com",
                first_name="Dana",
                last_name="Reyes",
                timezone="America/Chicago",
            )
        )
        await session.commit()

    report = await load_profile(engine, "demo")  # must not raise
    assert report.people == 9  # the cast count, not the table count


@pytest.mark.integration
@pytest.mark.anyio
async def test_booking_dataset_follows_a_preprovisioned_profile_id(engine: AsyncEngine) -> None:
    """A cast member first provisioned under a generated id keeps that id; the
    walkthrough rows must reference it through the people map, not FK-violate on a
    hard-coded historical id."""
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        session.add(
            UserProfile(
                id="uuid-from-a-real-signup",
                email="seed-casey@wandahealth.com",
                first_name="Casey",
                last_name="Ellis",
                timezone="America/New_York",
            )
        )
        await session.commit()

    await load_profile(engine, "booking-acceptance")  # must not IntegrityError

    from strata_core.domains.booking import AvailabilityPattern

    async with async_sessionmaker(engine)() as session:
        pattern = await session.get(AvailabilityPattern, "ap-c1")
        assert pattern is not None
        assert pattern.coach_id == "uuid-from-a-real-signup"  # followed the kept id


@pytest.mark.integration
@pytest.mark.anyio
async def test_cast_cognito_mappings_follow_login_capability(engine: AsyncEngine) -> None:
    """Login-capable cast members hold an active ``Cognito Sub`` mapping (the
    placeholder value, superseded by the auth seed's adoption); the means-test
    member — Sam Nguyen, no email, no login — holds NONE and
    still exists as a full, role-assigned person."""
    await load_profile(engine, "booking-acceptance")
    async with async_sessionmaker(engine)() as session:
        rows = (
            (
                await session.execute(
                    select(UserExternalId).where(
                        UserExternalId.type_id == EXTERNAL_ID_TYPE_IDS[COGNITO_SUB],
                        UserExternalId.ended_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        by_user = {row.user_id: row.external_id for row in rows}
        assert by_user["c-1"] == "seed-sub-casey"
        assert len([u for u in by_user if u.startswith(("c-", "m-", "a-"))]) == 9  # the demo cast
        assert by_user["m-6"] == "seed-sub-marta"  # post-migrated Marta is login-capable
        assert "m-3" not in by_user  # Sam Nguyen: no Cognito mapping at all

        sam = await session.get(UserProfile, "m-3")
        assert sam is not None
        assert sam.email is None  # a person with no email address
        assert sam.display_name is None  # no override; renders as "First Last"
        assert sam.effective_display_name == "Sam Nguyen"


@pytest.mark.integration
@pytest.mark.anyio
async def test_cast_reload_never_reverts_an_adopted_subject(engine: AsyncEngine) -> None:
    """Re-running a fixture profile must not supersede a real (adopted) pool
    subject with the placeholder — the old column semantics, preserved."""
    await load_profile(engine, "demo")
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        mapping = (
            await session.execute(
                select(UserExternalId).where(
                    UserExternalId.type_id == EXTERNAL_ID_TYPE_IDS[COGNITO_SUB],
                    UserExternalId.user_id == "c-1",
                    UserExternalId.ended_at.is_(None),
                )
            )
        ).scalar_one()
        mapping.ended_at = mapping.registered_at  # end the placeholder, as adoption does
        session.add(
            UserExternalId(
                user_id="c-1",
                type_id=EXTERNAL_ID_TYPE_IDS[COGNITO_SUB],
                external_id="real-pool-sub-casey",
                registered_at=mapping.registered_at,
            )
        )
        await session.commit()

    await load_profile(engine, "demo")  # re-run: must keep the adopted subject
    async with async_sessionmaker(engine)() as session:
        active = (
            await session.execute(
                select(UserExternalId).where(
                    UserExternalId.type_id == EXTERNAL_ID_TYPE_IDS[COGNITO_SUB],
                    UserExternalId.user_id == "c-1",
                    UserExternalId.ended_at.is_(None),
                )
            )
        ).scalar_one()
        assert active.external_id == "real-pool-sub-casey"


@pytest.mark.integration
@pytest.mark.anyio
async def test_clinical_demo_profile_loads_the_clinical_slice(engine: AsyncEngine) -> None:
    """The clinical slice converges deterministically and exercises the
    period shapes: Morgan's current hypertension + active prescription, Sam's
    same-code recurrence (two rows, two periods), and the demographics pair."""
    from strata_core.domains.clinical import (
        MemberDemographics,
        MemberDiagnosis,
        MemberMedication,
        MemberProcedure,
    )
    from strata_core.fixtures.clinical_data import (
        DEMOGRAPHICS,
        DIAGNOSES,
        MEDICATIONS,
        PROCEDURES,
    )

    report = await load_profile(engine, "clinical-demo")
    assert report.people == 9  # the demo cast, no booking extras

    async with async_sessionmaker(engine)() as session:
        morgan_id = (
            await session.execute(
                select(UserProfile.id).where(UserProfile.email == "seed-morgan@wandahealth.com")
            )
        ).scalar_one()
        morgan_dx = (
            await session.execute(
                select(MemberDiagnosis).where(MemberDiagnosis.member_user_id == morgan_id)
            )
        ).scalar_one()
        assert morgan_dx.code == "I10"
        assert morgan_dx.ends_on is None  # current
        morgan_rx = (
            await session.execute(
                select(MemberMedication).where(MemberMedication.member_user_id == morgan_id)
            )
        ).scalar_one()
        assert morgan_rx.ends_on is None  # active prescription

        sam_id = (
            await session.execute(
                select(UserProfile.id).where(UserProfile.email == "seed-sam@wandahealth.com")
            )
        ).scalar_one()
        sam_migraines = (
            (
                await session.execute(
                    select(MemberDiagnosis)
                    .where(
                        MemberDiagnosis.member_user_id == sam_id, MemberDiagnosis.code == "G43909"
                    )
                    .order_by(MemberDiagnosis.starts_on)
                )
            )
            .scalars()
            .all()
        )
        # Recurrence = multiple rows for the same code: historic then current.
        assert [dx.ends_on is None for dx in sam_migraines] == [False, True]

    # Idempotent: a second load converges to the same row counts.
    await load_profile(engine, "clinical-demo")
    async with async_sessionmaker(engine)() as session:
        for model, dataset in (
            (MemberDiagnosis, DIAGNOSES),
            (MemberProcedure, PROCEDURES),
            (MemberMedication, MEDICATIONS),
            (MemberDemographics, DEMOGRAPHICS),
        ):
            count = (await session.execute(select(func.count()).select_from(model))).scalar_one()
            assert count == len(dataset)
